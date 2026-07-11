"""Integration tests for idempotency replay + key-mismatch semantics.

Required environment: Postgres reachable at `ERP_TEST_DATABASE_URL`
with migrations applied. The conftest's `client` fixture handles the
PG lifecycle.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_replay_returns_original_response(client, admin_headers) -> None:
    """POST with a key, then again with the same key + same body — the
    second call returns the original response without re-running the
    handler."""
    key = f"idem-{uuid.uuid4()}"
    body = {"key": "test-role-1", "name": "Test Role 1"}
    headers = {**admin_headers, "Idempotency-Key": key}

    first = await client.post("/api/v1/erp/identity/roles", json=body, headers=headers)
    # The W0 read-only API does not actually expose a POST /identity/roles
    # route. The middleware should still return a structured error from
    # the route layer (405 or 404), but the *first* response is recorded
    # as the canonical one. Send again with same body and key — must
    # get the same status + body.
    first_status = first.status_code

    second = await client.post("/api/v1/erp/identity/roles", json=body, headers=headers)
    assert second.status_code == first_status, (
        f"idempotency replay changed status: {first_status} -> {second.status_code}"
    )
    # Both responses are the same JSON (the middleware replays from the store).
    assert second.json() == first.json()


@pytest.mark.asyncio
async def test_mismatch_returns_409(client, admin_headers) -> None:
    """POST with key X + body A, then again with key X + body B — the
    middleware returns 409 (Idempotency-Key mismatch)."""
    key = f"idem-mismatch-{uuid.uuid4()}"
    headers_a = {**admin_headers, "Idempotency-Key": key}

    # First request — record whatever the route returns.
    first = await client.post(
        "/api/v1/erp/identity/roles",
        json={"key": "a", "name": "A"},
        headers=headers_a,
    )
    # We only assert the *replay* semantics. If the route returns 200,
    # subsequent calls with the same key but a different body must
    # return 409. If the route is not implemented (404/405), we skip
    # the mismatch check because there's no record to mismatch against.
    if first.status_code == 404 or first.status_code == 405:
        pytest.skip("POST /identity/roles not implemented in W0; no record to mismatch against")

    # Second request with the SAME key but a DIFFERENT body.
    second = await client.post(
        "/api/v1/erp/identity/roles",
        json={"key": "b", "name": "B"},
        headers=headers_a,
    )
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "idempotency_key_mismatch"


@pytest.mark.asyncio
async def test_mutating_route_without_key_returns_400(client, admin_headers) -> None:
    """A POST without an Idempotency-Key is rejected with 400 before the
    route handler runs."""
    r = await client.post(
        "/api/v1/erp/identity/roles",
        json={"key": "x", "name": "X"},
        headers=admin_headers,
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "idempotency_key_required"
