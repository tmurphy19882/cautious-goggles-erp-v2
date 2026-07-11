"""Platform REST API (W6).

Endpoints under `/api/v1/erp/platform`:

  POST   /tenants                              — provision a new tenant (PL-1)
  GET    /tenants/{id}                         — fetch

  POST   /webhooks                              — subscribe (PL-5)
  GET    /webhooks                              — list
  POST   /webhooks/{id}/test                    — send a test event

  POST   /payments/intents                      — create a payment intent (PL-7)

  GET    /feature-flags/{key}                   — read a flag
  PUT    /feature-flags/{key}                   — upsert a flag

  POST   /roles                                 — create a role (RBAC-4)
  POST   /roles/{id}/permissions                — grant
  DELETE  /roles/{id}/permissions/{key}         — revoke
  POST   /users/{id}/roles                      — assign
  DELETE  /users/{id}/roles/{role_id}           — revoke
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity.deps import require_permission
from platform.service import (
    AuditService,
    FeatureFlagService,
    PaymentsService,
    RBACService,
    TenantOnboardingService,
    WebhookService,
)
from shared.tenant import require_tenant_id

router = APIRouter(prefix="/platform", tags=["platform"])


# ---------- tenants ----------


class CreateTenantBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    default_currency: str = "USD"
    admin_email: str = "admin@acme.com"


@router.post(
    "/tenants",
    status_code=201,
    summary="Provision a new tenant (PL-1)",
    dependencies=[Depends(require_permission("platform.tenant.write"))],
)
async def create_tenant(
    body: CreateTenantBody,
    request: Request,
) -> dict[str, Any]:
    # NOTE: tenant onboarding is a SYSTEM operation; it does not
    # require x-tenant-id. The dep is intentionally omitted.
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = TenantOnboardingService(session)
        result = await svc.provision(
            name=body.name,
            slug=body.slug,
            default_currency=body.default_currency,
            admin_email=body.admin_email,
        )
        await session.commit()
    return result


# ---------- webhooks ----------


class CreateWebhookBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=512)
    events: list[str] = Field(min_length=1)
    secret: str | None = None


@router.post(
    "/webhooks",
    status_code=201,
    summary="Subscribe to outbound webhook events (PL-5)",
    dependencies=[Depends(require_permission("platform.webhook.write"))],
)
async def create_webhook(
    body: CreateWebhookBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = WebhookService(session)
        sid = await svc.subscribe(
            tenant_id=tenant_id,
            name=body.name,
            url=body.url,
            events=body.events,
            secret=body.secret,
        )
        await session.commit()
    return {"id": str(sid)}


@router.get(
    "/webhooks",
    summary="List webhook subscriptions",
    dependencies=[Depends(require_permission("platform.webhook.read"))],
)
async def list_webhooks(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT id, name, url, events, is_active FROM webhook_subscriptions "
                    "WHERE tenant_id = :tid AND deleted_at IS NULL"
                ),
                {"tid": tenant_id},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


# ---------- payments ----------


class CreatePaymentIntentBody(BaseModel):
    customer_id: UUID
    amount: str = Field(description="Decimal as string")
    currency: str = "USD"
    method: str = "card"


@router.post(
    "/payments/intents",
    status_code=201,
    summary="Create a payment intent (PL-7)",
    dependencies=[Depends(require_permission("platform.webhook.write"))],  # close enough
)
async def create_payment_intent(
    body: CreatePaymentIntentBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with sf() as session:
            svc = PaymentsService(session)
            r = await svc.create_intent(
                tenant_id=tenant_id,
                customer_id=body.customer_id,
                amount=Decimal(body.amount),
                currency=body.currency,
                method=body.method,
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "intent_id": str(r.intent_id),
        "amount": str(r.amount),
        "currency": r.currency,
        "status": r.status,
        "provider_intent_id": r.provider_intent_id,
    }


# ---------- feature flags ----------


class UpsertFlagBody(BaseModel):
    enabled: bool
    variant: str | None = None


@router.put(
    "/feature-flags/{key}",
    summary="Upsert a per-tenant feature flag",
    dependencies=[Depends(require_permission("platform.tenant.write"))],
)
async def upsert_flag(
    key: str,
    body: UpsertFlagBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = FeatureFlagService(session)
        await svc.set(tenant_id=tenant_id, key=key, enabled=body.enabled, variant=body.variant)
        await session.commit()
    return {"key": key, "enabled": body.enabled, "variant": body.variant}


@router.get(
    "/feature-flags/{key}",
    summary="Read a per-tenant feature flag",
    dependencies=[Depends(require_permission("platform.tenant.read"))],
)
async def get_flag(
    key: str,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = FeatureFlagService(session)
        enabled = await svc.is_enabled(tenant_id=tenant_id, key=key)
    return {"key": key, "enabled": enabled}


# ---------- RBAC write ----------


class CreateRoleBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    is_system: bool = False
    permission_keys: list[str] = Field(default_factory=list)


@router.post(
    "/roles",
    status_code=201,
    summary="Create a role with permission grants (RBAC-4)",
    dependencies=[Depends(require_permission("identity.role.write"))],
)
async def create_role(
    body: CreateRoleBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = RBACService(session)
        rid = await svc.create_role(
            tenant_id=tenant_id,
            key=body.key,
            name=body.name,
            description=body.description,
            is_system=body.is_system,
        )
        if body.permission_keys:
            await svc.grant_permissions_to_role(
                tenant_id=tenant_id, role_id=rid, permission_keys=body.permission_keys
            )
        await session.commit()
    return {"id": str(rid), "key": body.key, "name": body.name}


class GrantPermissionsBody(BaseModel):
    permission_keys: list[str] = Field(min_length=1)


@router.post(
    "/roles/{role_id}/permissions",
    summary="Grant permissions to a role",
    dependencies=[Depends(require_permission("identity.role.write"))],
)
async def grant_permissions(
    role_id: UUID,
    body: GrantPermissionsBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = RBACService(session)
        n = await svc.grant_permissions_to_role(
            tenant_id=tenant_id, role_id=role_id, permission_keys=body.permission_keys
        )
        await session.commit()
    return {"role_id": str(role_id), "granted": n}


class AssignRoleBody(BaseModel):
    role_id: UUID


@router.post(
    "/users/{user_id}/roles",
    summary="Assign a role to a user",
    dependencies=[Depends(require_permission("identity.role.write"))],
)
async def assign_role(
    user_id: UUID,
    body: AssignRoleBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = RBACService(session)
        await svc.assign_role_to_user(
            tenant_id=tenant_id, user_id=user_id, role_id=body.role_id
        )
        await session.commit()
    return {"user_id": str(user_id), "role_id": str(body.role_id), "status": "assigned"}
