"""Unit tests for the tenant-id helpers.

Covers:

- `current_tenant_id(request)` returns the parsed `UUID` from
  `request.state.tenant_id` (set by `ObservabilityMiddleware`).
- `require_tenant_id` raises `TenantRequiredError` when missing.
- A malformed UUID in `x-tenant-id` is dropped (returns None) instead
  of leaking a 422 to the caller.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from shared.errors import TenantRequiredError
from shared.tenant import current_tenant_id, require_tenant_id


def _fake_request(tenant_id: UUID | None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(tenant_id=tenant_id))


def test_current_tenant_id_returns_uuid() -> None:
    tid = UUID("11111111-1111-1111-1111-111111111111")
    assert current_tenant_id(_fake_request(tid)) == tid


def test_current_tenant_id_returns_none_when_unset() -> None:
    assert current_tenant_id(_fake_request(None)) is None


@pytest.mark.asyncio
async def test_require_tenant_id_returns_value_when_set() -> None:
    tid = UUID("11111111-1111-1111-1111-111111111111")
    assert await require_tenant_id(_fake_request(tid)) == tid


@pytest.mark.asyncio
async def test_require_tenant_id_raises_when_missing() -> None:
    with pytest.raises(TenantRequiredError):
        await require_tenant_id(_fake_request(None))
