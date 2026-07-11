"""Integration tests for the v2 health and identity endpoints.

These require a real Postgres. The `client` fixture in conftest.py
skips the test gracefully if no DB is available.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_ok(client) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "erp-v2"


@pytest.mark.asyncio
async def test_ready_reports_db(client) -> None:
    r = await client.get("/ready")
    # Either 200 (db ok) or 503 (db not ok) — both are valid responses
    # the test runner just needs the JSON shape.
    body = r.json()
    assert "status" in body
    assert "checks" in body
    assert "database" in body["checks"]


@pytest.mark.asyncio
async def test_metrics_endpoint_exposed(client) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    # We should see at least the request metric names once a request hits.
    body_text = r.text
    assert "http_requests_total" in body_text or "# HELP" in body_text


@pytest.mark.asyncio
async def test_identity_permissions_catalog_is_global(client) -> None:
    """`/identity/permissions` is global (no `x-tenant-id` required)."""
    r = await client.get("/api/v1/erp/identity/permissions")
    assert r.status_code == 200
    catalog = r.json()
    # Catalog is seeded by migration 0100 — should have at least the
    # core identity, O2C, and P2C permissions.
    keys = {p["key"] for p in catalog}
    assert "identity.user.read" in keys
    assert "o2c.so.write" in keys
    assert "p2p.po.approve" in keys
    assert "platform.tenant.write" in keys


@pytest.mark.asyncio
async def test_identity_roles_requires_tenant_and_user(client) -> None:
    """`/identity/roles` requires both `x-tenant-id` and `x-user-id` (W0 stub)."""
    # No headers
    r = await client.get("/api/v1/erp/identity/roles")
    assert r.status_code in (400, 401)


@pytest.mark.asyncio
async def test_mutating_route_requires_idempotency_key(client) -> None:
    """Any POST without `Idempotency-Key` is rejected with 400."""
    r = await client.post("/api/v1/erp/identity/roles", json={"key": "test", "name": "Test"})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "idempotency_key_required"
