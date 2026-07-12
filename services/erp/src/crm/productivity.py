"""W7 — CRM notifications + custom fields + saved views.

W7 ships:
- `NotificationService` — in-app feed, email/SMS adapters are stubbed.
- `SavedViewService` — CRUD for per-user list customisations.
- `CustomFieldService` — CRUD for per-tenant schema extensions.

"""
from __future__ import annotations

import logging
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
