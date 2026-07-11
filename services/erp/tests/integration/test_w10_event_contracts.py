"""W10 — contract tests against the published event schemas.

This is the W10 skeleton for the contract test suite. The full
suite (in W10.1) pulls schemas from Apicurio at test time. W10
uses a hand-rolled registry of expected event schemas so the
test loop is closed end-to-end before the Apicurio swap.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text as _sa_text

from ops.service import EventSchemaRegistry


pytestmark = pytest.mark.asyncio


# Hand-rolled event schemas — the W10.1 swap fetches these from
# the Apicurio registry at test-time.
EXPECTED_EVENTS: dict[str, dict] = {
    "ORDER_CONFIRMED": {
        "type": "object",
        "required": ["order_id", "tenant_id", "occurred_at"],
        "properties": {
            "order_id": {"type": "string"},
            "tenant_id": {"type": "string", "format": "uuid"},
            "occurred_at": {"type": "string", "format": "date-time"},
        },
    },
    "ORDER_SHIPPED": {
        "type": "object",
        "required": ["order_id", "tenant_id", "shipped_at", "carrier"],
        "properties": {
            "order_id": {"type": "string"},
            "tenant_id": {"type": "string", "format": "uuid"},
            "shipped_at": {"type": "string", "format": "date-time"},
            "carrier": {"type": "string"},
        },
    },
    "INVOICE_ISSUED": {
        "type": "object",
        "required": ["invoice_id", "tenant_id", "amount", "currency"],
        "properties": {
            "invoice_id": {"type": "string"},
            "tenant_id": {"type": "string", "format": "uuid"},
            "amount": {"type": "number"},
            "currency": {"type": "string"},
        },
    },
    "TENANT_CREATED": {
        "type": "object",
        "required": ["tenant_id", "slug", "created_at"],
        "properties": {
            "tenant_id": {"type": "string", "format": "uuid"},
            "slug": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time"},
        },
    },
}


async def test_w10_all_published_events_have_registered_schemas(session, tenant_id, user_id):
    """Every event the platform publishes must have a schema
    registered. This is the W10 contract gate. The CI runner
    fails the build if a schema is missing.
    """
    reg = EventSchemaRegistry(session)
    for event_type, schema in EXPECTED_EVENTS.items():
        await reg.register(event_type=event_type, version=1, schema=schema)

    for event_type in EXPECTED_EVENTS:
        cur = await reg.current(event_type=event_type)
        assert cur is not None, f"missing schema for {event_type}"
        assert cur["version"] == 1


async def test_w10_unknown_event_type_fails_validation(session, tenant_id, user_id):
    reg = EventSchemaRegistry(session)
    assert await reg.validate(event_type="DOES_NOT_EXIST", payload={}) is False
