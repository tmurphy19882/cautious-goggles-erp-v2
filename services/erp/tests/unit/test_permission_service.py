"""Unit tests for the `PermissionService`.

These tests construct a fake session (not a real DB) and verify the
service's logic for loading a principal and checking permissions.
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
async def test_service_account_has_all_permissions() -> None:
    svc = PermissionService(session=MagicMock())
    p = Principal(
        user_id=USER,
        tenant_id=TENANT,
        is_service_account=True,
        permission_keys=frozenset(),  # even empty
    )
    # Service accounts short-circuit before checking the frozenset.
    assert await svc.can(p, "erp.party.write") is True
    assert await svc.can(p, "anything.really") is True


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
