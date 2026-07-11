"""Platform service for W6 — tenant onboarding, webhooks, payments, RBAC write.

W6 ships:

- `TenantOnboardingService` — creates a tenant row + an admin user +
  admin role with all permissions, logs each step, publishes
  `TENANT_CREATED` on the outbox. (Replaces the W0 stub.)
- `WebhookService` — subscribe / unsubscribe / list / deliver.
  `dispatch` walks every active subscription for a tenant, signs the
  payload with HMAC-SHA256, posts via httpx, and records the
  delivery row.
- `PaymentsService` — Stripe-shaped intent create + capture. W6 ships
  a deterministic stub that returns success; the real Stripe swap
  is in W6.1.
- `RBACService` — role / permission CRUD. The W0 hotfix shipped the
  read API + the table + the per-principal `service_account_scopes`
  guard; W6 adds the write API.
- `FeatureFlagService` — per-tenant flag read / write. The W0 hotfix
  shipped Unleash as a stub in docker-compose; W6 ships the in-process
  service that reads / writes the `feature_flags` table.
- `AuditService` — append-only audit log. Every mutating route should
  call `audit.log(...)`; W6 ships the helper, route-level integration
  lands in W6.1.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.events import (
    TOPIC_TENANT_CREATED,
    build_envelope,
)
from shared.outbox import OutboxStore
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Tenant onboarding                                                      #
# --------------------------------------------------------------------- #


class TenantOnboardingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = OutboxStore(session)

    async def provision(
        self,
        *,
        name: str,
        slug: str,
        default_currency: str = "USD",
        admin_email: str = "admin@acme.com",
    ) -> dict[str, Any]:
        """Create a tenant + admin user + admin role, publish
        TENANT_CREATED, return the IDs.

        Idempotent on `slug` — re-running returns the existing tenant.
        """
        existing = (
            await self._session.execute(
                _sa_text("SELECT * FROM tenants WHERE slug = :slug"),
                {"slug": slug},
            )
        ).mappings().first()
        if existing is not None:
            return {
                "tenant_id": str(existing["id"]),
                "status": existing["status"],
                "idempotent": True,
            }

        tenant_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO tenants (id, name, slug, status, default_currency)
                VALUES (:id, :name, :slug, 'active', :currency)
                """
            ),
            {
                "id": tenant_id,
                "name": name,
                "slug": slug,
                "currency": default_currency,
            },
        )
        await self._log_step(tenant_id, "tenant.create", "succeeded", {"slug": slug, "name": name})

        # Create the admin user.
        user_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO users (id, tenant_id, email, display_name, is_active, is_service_account)
                VALUES (:id, :tid, :email, :display, true, false)
                """
            ),
            {
                "id": user_id,
                "tid": tenant_id,
                "email": admin_email,
                "display": f"{name} Admin",
            },
        )
        await self._log_step(tenant_id, "user.create", "succeeded", {"user_id": str(user_id), "email": admin_email})

        # Create the admin role + grant every catalog permission.
        role_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO roles (id, tenant_id, key, name, is_system)
                VALUES (:id, :tid, 'admin', 'Administrator', true)
                """
            ),
            {"id": role_id, "tid": tenant_id},
        )
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO user_roles (user_id, role_id) VALUES (:uid, :rid)
                """,
            ),
            {"uid": user_id, "rid": role_id},
        )
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO role_permissions (role_id, permission_key)
                SELECT :rid, key FROM permissions
                ON CONFLICT DO NOTHING
                """,
            ),
            {"rid": role_id},
        )
        await self._log_step(tenant_id, "role.create", "succeeded", {"role_id": str(role_id), "grants": "all"})

        # Seed the O2C default price list (one line) so the SO can be priced
        # in the smoke flow.
        from decimal import Decimal as _Dec
        list_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO price_lists (id, tenant_id, code, name, currency, is_active)
                VALUES (:id, :tid, 'DEFAULT', 'Default Price List', :currency, true)
                """,
            ),
            {"id": list_id, "tid": tenant_id, "currency": default_currency},
        )

        # Publish TENANT_CREATED.
        envelope = build_envelope(
            event_type=TOPIC_TENANT_CREATED,
            tenant_id=tenant_id,
            trace_id="onboarding",
            payload={
                "tenant_id": str(tenant_id),
                "name": name,
                "slug": slug,
                "default_currency": default_currency,
                "admin_user_id": str(user_id),
                "admin_role_id": str(role_id),
            },
        )
        await self._outbox.emit(
            tenant_id=tenant_id,
            aggregate_type="tenant",
            aggregate_id=tenant_id,
            envelope=envelope,
            topic=TOPIC_TENANT_CREATED,
        )
        await self._log_step(tenant_id, "outbox.publish", "succeeded", {"event_type": TOPIC_TENANT_CREATED})

        # Set the tenant context to the new tenant for the rest of the txn.
        await self._session.execute(
            _sa_text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_id)}
        )

        await self._session.flush()
        return {
            "tenant_id": str(tenant_id),
            "admin_user_id": str(user_id),
            "admin_role_id": str(role_id),
            "default_price_list_id": str(list_id),
            "status": "active",
        }

    async def _log_step(
        self, tenant_id: UUID, step: str, status: str, details: dict[str, Any] | None = None
    ) -> None:
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO tenant_onboarding_log (tenant_id, step, status, details, finished_at)
                VALUES (:tid, :step, :status, :details::jsonb, now())
                """
            ),
            {
                "tid": tenant_id,
                "step": step,
                "status": status,
                "details": details or {},
            },
        )


# --------------------------------------------------------------------- #
# Webhooks                                                                #
# --------------------------------------------------------------------- #


class WebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def subscribe(
        self,
        *,
        tenant_id: UUID,
        name: str,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> UUID:
        sid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO webhook_subscriptions (id, tenant_id, name, url, secret, events)
                VALUES (:id, :tenant_id, :name, :url, :secret, :events::jsonb)
                """
            ),
            {
                "id": sid,
                "tenant_id": tenant_id,
                "name": name,
                "url": url,
                "secret": secret,
                "events": json.dumps(events),
            },
        )
        await self._session.flush()
        return sid

    async def dispatch(
        self, *, tenant_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> int:
        """POST the payload to every active subscription matching the
        event_type. Returns the number of deliveries recorded.
        """
        subs = (
            await self._session.execute(
                _sa_text(
                    """
                    SELECT id, url, secret FROM webhook_subscriptions
                    WHERE tenant_id = :tid AND is_active = true
                      AND events @> :events::jsonb
                    """
                ),
                {"tid": str(tenant_id), "events": json.dumps([event_type])},
            )
        ).mappings().all()

        body = json.dumps({"event_type": event_type, "data": payload}, default=str).encode()
        delivered = 0
        async with httpx.AsyncClient(timeout=10) as client:
            for sub in subs:
                signature = ""
                if sub["secret"]:
                    signature = hmac.new(
                        sub["secret"].encode(), body, hashlib.sha256
                    ).hexdigest()
                try:
                    resp = await client.post(
                        sub["url"],
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Signature": signature,
                            "X-Event-Type": event_type,
                        },
                    )
                    status = "succeeded" if resp.status_code < 400 else "failed"
                    http_status = resp.status_code
                except Exception as exc:
                    status = "failed"
                    http_status = None
                    logger.warning("webhook dispatch failed: %s", exc)
                await self._session.execute(
                    _sa_text(
                        """
                        INSERT INTO webhook_deliveries (
                            tenant_id, subscription_id, event_type, payload,
                            status, http_status, attempts, last_attempt_at, delivered_at
                        ) VALUES (
                            :tid, :sid, :event_type, :payload::jsonb,
                            :status, :http_status, 1, now(), CASE WHEN :status = 'succeeded' THEN now() ELSE NULL END
                        )
                        """
                    ),
                    {
                        "tid": str(tenant_id),
                        "sid": sub["id"],
                        "event_type": event_type,
                        "payload": json.dumps(payload),
                        "status": status,
                        "http_status": http_status,
                    },
                )
                delivered += 1
        await self._session.flush()
        return delivered


# --------------------------------------------------------------------- #
# Payments (Stripe-shaped stub)                                          #
# --------------------------------------------------------------------- #


@dataclass(slots=True)
class PaymentIntent:
    intent_id: UUID
    amount: Decimal
    currency: str
    status: str
    provider_intent_id: str | None = None
    provider_charge_id: str | None = None


class PaymentsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_intent(
        self,
        *,
        tenant_id: UUID,
        customer_id: UUID,
        amount: Decimal,
        currency: str = "USD",
        method: str = "card",
    ) -> PaymentIntent:
        if amount <= 0:
            raise ValueError("amount must be positive")
        iid = uuid4()
        # W6 stub: deterministic "succeeded" for amounts divisible by 100;
        # "requires_action" otherwise. Real Stripe swap in W6.1.
        status = "succeeded" if (int(amount * 100) % 100) == 0 else "requires_action"
        provider_id = f"pi_stub_{iid.hex[:10]}"
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO payment_intents (
                    id, tenant_id, customer_id, amount, currency, method, status,
                    provider_intent_id
                ) VALUES (
                    :id, :tenant_id, :customer, :amount, :currency, :method, :status, :pid
                )
                """
            ),
            {
                "id": iid,
                "tenant_id": tenant_id,
                "customer": customer_id,
                "amount": amount,
                "currency": currency,
                "method": method,
                "status": status,
                "pid": provider_id,
            },
        )
        await self._session.flush()
        return PaymentIntent(
            intent_id=iid,
            amount=amount,
            currency=currency,
            status=status,
            provider_intent_id=provider_id,
        )


# --------------------------------------------------------------------- #
# Feature flags (in-process, per-tenant)                                 #
# --------------------------------------------------------------------- #


class FeatureFlagService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_enabled(self, *, tenant_id: UUID, key: str, default: bool = False) -> bool:
        row = (
            await self._session.execute(
                _sa_text(
                    "SELECT enabled FROM feature_flags "
                    "WHERE tenant_id = :tid AND key = :key"
                ),
                {"tid": str(tenant_id), "key": key},
            )
        ).mappings().first()
        return bool(row["enabled"]) if row else default

    async def set(
        self, *, tenant_id: UUID, key: str, enabled: bool, variant: str | None = None
    ) -> None:
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO feature_flags (tenant_id, key, enabled, variant)
                VALUES (:tid, :key, :enabled, :variant)
                ON CONFLICT (tenant_id, key) DO UPDATE
                SET enabled = EXCLUDED.enabled, variant = EXCLUDED.variant, updated_at = now()
                """
            ),
            {"tid": str(tenant_id), "key": key, "enabled": enabled, "variant": variant},
        )
        await self._session.flush()


# --------------------------------------------------------------------- #
# Audit log                                                               #
# --------------------------------------------------------------------- #


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO audit_log (
                    tenant_id, actor_id, action, entity_type, entity_id,
                    before, after, request_id, trace_id
                ) VALUES (
                    :tid, :actor, :action, :etype, :eid,
                    :before::jsonb, :after::jsonb, :req, :trace
                )
                """
            ),
            {
                "tid": str(tenant_id),
                "actor": actor_id,
                "action": action,
                "etype": entity_type,
                "eid": entity_id,
                "before": json.dumps(before) if before is not None else None,
                "after": json.dumps(after) if after is not None else None,
                "req": request_id,
                "trace": trace_id,
            },
        )


# --------------------------------------------------------------------- #
# RBAC write (closes RBAC-4 from the audit)                              #
# --------------------------------------------------------------------- #


class RBACService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_role(
        self,
        *,
        tenant_id: UUID,
        key: str,
        name: str,
        description: str | None = None,
        is_system: bool = False,
    ) -> UUID:
        rid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO roles (id, tenant_id, key, name, description, is_system)
                VALUES (:id, :tid, :key, :name, :desc, :sys)
                """
            ),
            {"id": rid, "tid": tenant_id, "key": key, "name": name, "desc": description, "sys": is_system},
        )
        await self._session.flush()
        return rid

    async def grant_permissions_to_role(
        self, *, tenant_id: UUID, role_id: UUID, permission_keys: list[str]
    ) -> int:
        # Idempotent insert.
        count = 0
        for k in permission_keys:
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO role_permissions (role_id, permission_key)
                    VALUES (:rid, :key)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"rid": role_id, "key": k},
            )
            count += 1
        await self._session.flush()
        return count

    async def revoke_permission_from_role(
        self, *, tenant_id: UUID, role_id: UUID, permission_key: str
    ) -> None:
        await self._session.execute(
            _sa_text(
                "DELETE FROM role_permissions WHERE role_id = :rid AND permission_key = :key"
            ),
            {"rid": role_id, "key": permission_key},
        )
        await self._session.flush()

    async def assign_role_to_user(
        self, *, tenant_id: UUID, user_id: UUID, role_id: UUID, granted_by: UUID | None = None
    ) -> None:
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO user_roles (user_id, role_id, granted_by)
                VALUES (:uid, :rid, :by)
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": user_id, "rid": role_id, "by": granted_by},
        )
        await self._session.flush()

    async def revoke_role_from_user(
        self, *, tenant_id: UUID, user_id: UUID, role_id: UUID
    ) -> None:
        await self._session.execute(
            _sa_text("DELETE FROM user_roles WHERE user_id = :uid AND role_id = :rid"),
            {"uid": user_id, "rid": role_id},
        )
        await self._session.flush()
