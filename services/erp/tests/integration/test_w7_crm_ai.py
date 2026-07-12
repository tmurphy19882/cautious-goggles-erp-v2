"""W7 — CRM AI + notifications + saved views + custom fields.

Happy-path integration tests for the W7 surface.
"""
from __future__ import annotations

import pytest

from crm.ai import CustomFieldService, NotificationService, SavedViewService


pytestmark = pytest.mark.asyncio


async def test_w7_notifications_send_and_list(session, tenant_id, user_id):
    svc = NotificationService(session)
    nid = await svc.send(
        tenant_id=tenant_id,
        user_id=user_id,
        kind="in_app",
        subject_type="ticket",
        subject_id=None,
        title="New ticket assigned",
        body="Ticket #123 has been assigned to you.",
    )
    assert nid is not None

    items = await svc.list_for_user(tenant_id=tenant_id, user_id=user_id, unread_only=True)
    assert any(i["id"] == nid for i in items)
    assert any(i["title"] == "New ticket assigned" for i in items)

    await svc.mark_read(tenant_id=tenant_id, notification_id=nid)
    items = await svc.list_for_user(tenant_id=tenant_id, user_id=user_id, unread_only=True)
    assert not any(i["id"] == nid for i in items)


async def test_w7_saved_views_create_and_list(session, tenant_id, user_id):
    svc = SavedViewService(session)
    vid = await svc.create(
        tenant_id=tenant_id,
        user_id=user_id,
        name="My open tickets",
        entity_type="ticket",
        filters={"status": "open"},
        sort=[{"field": "created_at", "dir": "desc"}],
    )
    assert vid is not None
    items = await svc.list_for_user(tenant_id=tenant_id, user_id=user_id, entity_type="ticket")
    assert any(i["id"] == vid for i in items)


async def test_w7_custom_field_define_and_set(session, tenant_id):
    svc = CustomFieldService(session)
    fid = await svc.define(
        tenant_id=tenant_id,
        entity_type="customer",
        key="lead_source",
        label="Lead Source",
        field_type="select",
        options={"options": ["web", "referral", "trade_show"]},
        is_required=False,
    )
    assert fid is not None
    # Use a dummy entity_id
    from uuid import uuid4
    eid = uuid4()
    await svc.set_value(
        tenant_id=tenant_id,
        field_id=fid,
        entity_id=eid,
        value_text="referral",
    )
    from sqlalchemy import text as _sa_text
    rows = (
        await session.execute(
            _sa_text(
                "SELECT value_text FROM custom_field_values "
                "WHERE field_id = :fid AND entity_id = :eid"
            ),
            {"fid": fid, "eid": eid},
        )
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["value_text"] == "referral"
