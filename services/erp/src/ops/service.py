"""W10 — Operational readiness services.

- `DLQService` — push failed outbox messages to the DLQ with
  original payload + error context; mark resolved.
- `EventSchemaRegistry` — central registry of event_type -> schema.
  The W10.1 swap is an Apicurio client. W10 ships an in-DB
  registry.
- `MetricsRecorder` — write per-request metrics to
  `api_request_metrics` for the per-tenant / per-route
  dashboards.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# DLQ                                                                     #
# --------------------------------------------------------------------- #


class DLQService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def push(
        self,
        *,
        tenant_id: UUID,
        original_outbox_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        error: str,
        attempts: int = 0,
    ) -> UUID:
        dlq_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO outbox_dlq (
                    id, tenant_id, original_outbox_id, event_type, payload, error, attempts
                ) VALUES (
                    :id, :tenant_id, :oid, :etype, :payload::jsonb, :err, :att
                )
                """
            ),
            {
                "id": dlq_id, "tenant_id": tenant_id, "oid": original_outbox_id,
                "etype": event_type, "payload": json.dumps(payload),
                "err": error, "att": attempts,
            },
        )
        await self._session.flush()
        return dlq_id

    async def list_unresolved(
        self, *, tenant_id: UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                _sa_text(
                    "SELECT id, event_type, error, attempts, last_attempt_at, created_at "
                    "FROM outbox_dlq WHERE tenant_id = :tid AND resolved_at IS NULL "
                    "ORDER BY last_attempt_at DESC LIMIT :limit"
                ),
                {"tid": tenant_id, "limit": max(1, min(200, limit))},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def resolve(
        self, *, tenant_id: UUID, dlq_id: UUID, note: str
    ) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE outbox_dlq SET resolved_at = :now, resolution_note = :note "
                "WHERE id = :id AND tenant_id = :tid"
            ),
            {"now": utcnow(), "note": note, "id": dlq_id, "tid": tenant_id},
        )
        await self._session.flush()


# --------------------------------------------------------------------- #
# Event schema registry (Apicurio-shaped; in-DB W10 stub)                #
# --------------------------------------------------------------------- #


class EventSchemaRegistry:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self, *, event_type: str, version: int, schema: dict[str, Any]
    ) -> None:
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO event_schema_versions (event_type, version, schema_json)
                VALUES (:etype, :ver, :schema::jsonb)
                ON CONFLICT (event_type) DO UPDATE
                SET version = EXCLUDED.version,
                    schema_json = EXCLUDED.schema_json,
                    published_at = now()
                """
            ),
            {"etype": event_type, "ver": version, "schema": json.dumps(schema)},
        )
        await self._session.flush()

    async def current(self, *, event_type: str) -> dict[str, Any] | None:
        row = (
            await self._session.execute(
                _sa_text(
                    "SELECT event_type, version, schema_json, published_at "
                    "FROM event_schema_versions WHERE event_type = :etype"
                ),
                {"etype": event_type},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def validate(
        self, *, event_type: str, payload: dict[str, Any]
    ) -> bool:
        """W10 stub: only checks the event_type is registered. The
        W10.1 swap is a real JSON-Schema validator against the
        Apicurio-published schema.
        """
        rec = await self.current(event_type=event_type)
        return rec is not None


# --------------------------------------------------------------------- #
# Per-tenant metrics                                                      #
# --------------------------------------------------------------------- #


class MetricsRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        tenant_id: UUID | None,
        route: str,
        method: str,
        status: int,
        latency_ms: int,
        user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        # If there's no tenant (system request), use the SENTINEL so
        # the row is visible to the metrics dashboards without
        # tripping the FORCE RLS policy.
        tid = tenant_id or UUID("00000000-0000-0000-0000-000000000000")
        try:
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO api_request_metrics (
                        tenant_id, route, method, status, latency_ms, user_id, request_id
                    ) VALUES (
                        :tid, :route, :method, :status, :lat, :uid, :rid
                    )
                    """
                ),
                {
                    "tid": tid, "route": route, "method": method,
                    "status": status, "lat": latency_ms,
                    "uid": user_id, "rid": request_id,
                },
            )
            await self._session.flush()
        except Exception as exc:  # pragma: no cover
            logger.warning("metrics record failed: %s", exc)
