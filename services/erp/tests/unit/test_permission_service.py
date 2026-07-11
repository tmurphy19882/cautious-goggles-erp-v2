"""Unit tests for the `PermissionService` in-memory path.

These tests construct a fake session (not a real DB) and verify the
service's logic for `can()` and `assert_can()`. The DB-backed
`load_principal` path is tested separately in
`tests/unit/test_permission_service_db.py` (BUG-020 split).
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from identity.service import PermissionDenied, PermissionService, Principal

TENANT = UUID("11111111-1111-1111-1111-111111111111")
USER = UUID("22222222-2222-2222-2222-222222222222")


def _principal_with(*keys: str) -> Principal:
    return Principal(
        user_id=USER,
        tenant_id=TENANT,
        is_service_account=False,
        permission_keys=frozenset(keys),
    )


@pytest.mark.asyncio
async def test_can_returns_true_for_granted_key() -> None:
    svc = PermissionService(session=MagicMock())
    p = _principal_with("erp.so.read", "erp.so.write")
    assert await svc.can(p, "erp.so.read") is True
    assert await svc.can(p, "erp.so.write") is True


@pytest.mark.asyncio
async def test_can_returns_false_for_ungranted_key() -> None:
    svc = PermissionService(session=MagicMock())
    p = _principal_with("erp.so.read")
    assert await svc.can(p, "erp.so.cancel") is False


@pytest.mark.asyncio
async def test_service_account_with_empty_scopes_is_denied() -> None:
    """BUG-006: a service account with empty `service_account_scopes`
    is denied every permission. The bare `is_service_account` flag is
    no longer enough — there's a per-principal allow-list."""
    svc = PermissionService(session=MagicMock())
    p = Principal(
        user_id=USER,
        tenant_id=TENANT,
        is_service_account=True,
        permission_keys=frozenset(),
        service_account_scopes=frozenset(),
    )
    assert await svc.can(p, "erp.party.write") is False
    assert await svc.can(p, "anything.really") is False


@pytest.mark.asyncio
async def test_service_account_with_explicit_scope_is_allowed() -> None:
    """BUG-006: a service account whose `service_account_scopes` includes
    the requested key is allowed (and only those keys)."""
    svc = PermissionService(session=MagicMock())
    p = Principal(
        user_id=USER,
        tenant_id=TENANT,
        is_service_account=True,
        permission_keys=frozenset(),
        service_account_scopes=frozenset({"erp.so.read", "erp.party.read"}),
    )
    assert await svc.can(p, "erp.so.read") is True
    assert await svc.can(p, "erp.party.read") is True
    assert await svc.can(p, "erp.so.write") is False  # not in scope
    assert await svc.can(p, "finance.period.close") is False  # not in scope


@pytest.mark.asyncio
async def test_assert_can_raises_when_denied() -> None:
    svc = PermissionService(session=MagicMock())
    p = _principal_with("erp.so.read")
    with pytest.raises(PermissionDenied) as exc:
        await svc.assert_can(p, "erp.so.write")
    assert exc.value.permission_key == "erp.so.write"


@pytest.mark.asyncio
async def test_assert_can_silent_when_allowed() -> None:
    svc = PermissionService(session=MagicMock())
    p = _principal_with("erp.so.write")
    await svc.assert_can(p, "erp.so.write")  # should not raise
