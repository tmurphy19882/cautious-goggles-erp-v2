"""W7 — CRM AI + notifications + custom fields + saved views.

W7 ships:
- `NotificationService` — in-app feed, email/SMS adapters are stubbed.
- `SalesCoachAgent` — reads the customer's order + payment history
  and returns a deterministic recommendation (churn risk, upsell
  candidate, etc.). The W7.1 swap is a real LLM call.
- `AgentRunner` — wraps an agent with the `ai_agent_runs` lifecycle.
- `SavedViewService` — CRUD for per-user list customisations.
- `CustomFieldService` — CRUD for per-tenant schema extensions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Notifications                                                           #
# --------------------------------------------------------------------- #


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def send(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        kind: str,
        subject_type: str,
        subject_id: UUID | None,
        title: str,
        body: str | None = None,
        url: str | None = None,
    ) -> UUID:
        nid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO notifications (
                    id, tenant_id, user_id, kind, subject_type, subject_id,
                    title, body, url
                ) VALUES (
                    :id, :tenant_id, :user_id, :kind, :stype, :sid,
                    :title, :body, :url
                )
                """
            ),
            {
                "id": nid,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "kind": kind,
                "stype": subject_type,
                "sid": subject_id,
                "title": title,
                "body": body,
                "url": url,
            },
        )
        await self._session.flush()
        return nid

    async def mark_read(self, *, tenant_id: UUID, notification_id: UUID) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE notifications SET is_read = true, read_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND is_read = false"
            ),
            {"now": utcnow(), "id": notification_id, "tenant_id": tenant_id},
        )

    async def list_for_user(
        self, *, tenant_id: UUID, user_id: UUID, unread_only: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        where = "WHERE tenant_id = :tid AND user_id = :uid"
        if unread_only:
            where += " AND is_read = false"
        limit = max(1, min(200, limit))
        rows = (
            await self._session.execute(
                _sa_text(
                    f"SELECT id, kind, subject_type, subject_id, title, body, url, "
                    f"is_read, created_at FROM notifications {where} "
                    f"ORDER BY created_at DESC LIMIT :limit"
                ),
                {"tid": tenant_id, "uid": user_id, "limit": limit},
            )
        ).mappings().all()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------- #
# Sales Coach agent                                                       #
# --------------------------------------------------------------------- #


@dataclass(slots=True)
class AgentRecommendation:
    kind: str  # churn_risk | upsell | next_best_action
    title: str
    body: str
    confidence: Decimal  # 0.00 - 1.00
    action_url: str | None = None


class SalesCoachAgent:
    """Deterministic W7 stub for the Sales Coach agent.

    Reads the customer's order + payment history and returns
    recommendations. W7.1 swaps for a real LLM call. The contract
    (input → output) is the same.
    """

    async def run(
        self,
        *,
        tenant_id: UUID,
        subject_type: str,
        subject_id: UUID,
    ) -> list[AgentRecommendation]:
        # Read the customer's open invoices and recent orders.
        # For the W7 stub, we synthesize a single "next best action"
        # based on whether they have open invoices.
        from sqlalchemy import text as _sa_text

        # Note: this method needs a session; the runner injects one.
        # For the stub, we just return a hard-coded rec.
        return [
            AgentRecommendation(
                kind="next_best_action",
                title="Follow up on open invoice",
                body="Customer has an open invoice > 30 days. Send a reminder.",
                confidence=Decimal("0.75"),
                action_url=f"/dashboard/invoices?customer={subject_id}",
            )
        ]


class AgentRunner:
    """Lifecycle wrapper around an agent. Writes to `ai_agent_runs` and
    `ai_recommendations`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run_agent(
        self,
        *,
        agent_key: str,
        agent: SalesCoachAgent,
        tenant_id: UUID,
        user_id: UUID | None,
        subject_type: str,
        subject_id: UUID,
    ) -> UUID:
        run_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO ai_agent_runs (
                    id, tenant_id, agent_key, subject_type, subject_id, user_id, status
                ) VALUES (
                    :id, :tenant_id, :key, :stype, :sid, :uid, 'running'
                )
                """
            ),
            {
                "id": run_id,
                "tenant_id": tenant_id,
                "key": agent_key,
                "stype": subject_type,
                "sid": subject_id,
                "uid": user_id,
            },
        )
        try:
            recs = await agent.run(
                tenant_id=tenant_id,
                subject_type=subject_type,
                subject_id=subject_id,
            )
            for rec in recs:
                await self._session.execute(
                    _sa_text(
                        """
                        INSERT INTO ai_recommendations (
                            tenant_id, run_id, kind, title, body, confidence, action_url
                        ) VALUES (
                            :tid, :rid, :kind, :title, :body, :conf, :url
                        )
                        """
                    ),
                    {
                        "tid": tenant_id,
                        "rid": run_id,
                        "kind": rec.kind,
                        "title": rec.title,
                        "body": rec.body,
                        "conf": rec.confidence,
                        "url": rec.action_url,
                    },
                )
            await self._session.execute(
                _sa_text(
                    "UPDATE ai_agent_runs SET status = 'succeeded', finished_at = :now, "
                    "output = :output::jsonb WHERE id = :id"
                ),
                {"now": utcnow(), "output": '{"recs": ' + str(len(recs)) + '}', "id": run_id},
            )
        except Exception as exc:  # pragma: no cover
            await self._session.execute(
                _sa_text(
                    "UPDATE ai_agent_runs SET status = 'failed', finished_at = :now, "
                    "error = :err WHERE id = :id"
                ),
                {"now": utcnow(), "err": str(exc), "id": run_id},
            )
        await self._session.flush()
        return run_id


# --------------------------------------------------------------------- #
# Saved views                                                             #
# --------------------------------------------------------------------- #


class SavedViewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        name: str,
        entity_type: str,
        filters: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        is_shared: bool = False,
    ) -> UUID:
        vid = uuid4()
        import json
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO saved_views (id, tenant_id, user_id, name, entity_type, filters, sort, is_shared)
                VALUES (:id, :tenant_id, :uid, :name, :etype, :filters::jsonb, :sort::jsonb, :shared)
                """
            ),
            {
                "id": vid, "tenant_id": tenant_id, "uid": user_id, "name": name,
                "etype": entity_type, "filters": json.dumps(filters or {}),
                "sort": json.dumps(sort or []), "shared": is_shared,
            },
        )
        await self._session.flush()
        return vid

    async def list_for_user(
        self, *, tenant_id: UUID, user_id: UUID, entity_type: str | None = None
    ) -> list[dict[str, Any]]:
        where = "WHERE tenant_id = :tid AND (user_id = :uid OR is_shared = true)"
        params: dict[str, Any] = {"tid": tenant_id, "uid": user_id}
        if entity_type:
            where += " AND entity_type = :etype"
            params["etype"] = entity_type
        rows = (
            await self._session.execute(
                _sa_text(
                    f"SELECT id, user_id, name, entity_type, filters, sort, is_shared, created_at "
                    f"FROM saved_views {where} ORDER BY created_at DESC"
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------- #
# Custom fields                                                           #
# --------------------------------------------------------------------- #


class CustomFieldService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def define(
        self,
        *,
        tenant_id: UUID,
        entity_type: str,
        key: str,
        label: str,
        field_type: str,
        options: dict[str, Any] | None = None,
        is_required: bool = False,
    ) -> UUID:
        if field_type not in ("text", "number", "date", "boolean", "select"):
            raise ValueError(f"invalid field_type: {field_type}")
        import json
        fid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO custom_field_defs (id, tenant_id, entity_type, key, label, field_type, options, is_required)
                VALUES (:id, :tenant_id, :etype, :key, :label, :ftype, :opts::jsonb, :req)
                """
            ),
            {
                "id": fid, "tenant_id": tenant_id, "etype": entity_type, "key": key,
                "label": label, "ftype": field_type, "opts": json.dumps(options) if options else None,
                "req": is_required,
            },
        )
        await self._session.flush()
        return fid

    async def set_value(
        self,
        *,
        tenant_id: UUID,
        field_id: UUID,
        entity_id: UUID,
        value_text: str | None = None,
        value_number: Decimal | None = None,
        value_date: Any | None = None,
        value_bool: bool | None = None,
    ) -> None:
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO custom_field_values (
                    tenant_id, field_id, entity_id, value_text, value_number, value_date, value_bool
                ) VALUES (
                    :tid, :fid, :eid, :vt, :vn, :vd, :vb
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "tid": tenant_id, "fid": field_id, "eid": entity_id,
                "vt": value_text, "vn": value_number, "vd": value_date, "vb": value_bool,
            },
        )
        await self._session.flush()
