"""W10 — Operational readiness integration tests."""
from __future__ import annotations

from uuid import uuid4

import pytest

from ops.service import DLQService, EventSchemaRegistry, MetricsRecorder


pytestmark = pytest.mark.asyncio


async def test_w10_dlq_push_and_resolve(session, tenant_id, user_id):
    svc = DLQService(session)
    outbox_id = uuid4()
    dlq_id = await svc.push(
        tenant_id=tenant_id,
        original_outbox_id=outbox_id,
        event_type="ORDER_CONFIRMED",
        payload={"order_id": "abc-123", "amount": 100.0},
        error="consumer timed out after 30s",
        attempts=5,
    )
    assert dlq_id is not None
    items = await svc.list_unresolved(tenant_id=tenant_id)
    assert any(i["id"] == dlq_id for i in items)
    await svc.resolve(tenant_id=tenant_id, dlq_id=dlq_id, note="manual reprocess succeeded")
    items = await svc.list_unresolved(tenant_id=tenant_id)
    assert not any(i["id"] == dlq_id for i in items)


async def test_w10_event_schema_registry(session, tenant_id, user_id):
    svc = EventSchemaRegistry(session)
    await svc.register(
        event_type="ORDER_CONFIRMED",
        version=1,
        schema={"type": "object", "required": ["order_id", "tenant_id"]},
    )
    cur = await svc.current(event_type="ORDER_CONFIRMED")
    assert cur is not None
    assert cur["version"] == 1
    # Validate is W10 stub — only checks registration
    assert await svc.validate(
        event_type="ORDER_CONFIRMED", payload={"order_id": "x", "tenant_id": "y"}
    ) is True
    assert await svc.validate(
        event_type="UNREGISTERED", payload={}
    ) is False


async def test_w10_metrics_record(session, tenant_id, user_id):
    svc = MetricsRecorder(session)
    await svc.record(
        tenant_id=tenant_id,
        route="/api/v1/erp/orders",
        method="POST",
        status=201,
        latency_ms=42,
        user_id=user_id,
    )
    from sqlalchemy import text as _sa_text
    rows = (
        await session.execute(
            _sa_text(
                "SELECT route, method, status, latency_ms FROM api_request_metrics "
                "WHERE tenant_id = :tid"
            ),
            {"tid": tenant_id},
        )
    ).mappings().all()
    assert len(rows) == 1
    assert rows[0]["status"] == 201
    assert rows[0]["latency_ms"] == 42
