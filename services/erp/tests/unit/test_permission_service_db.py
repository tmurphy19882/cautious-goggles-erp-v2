"""Integration tests for `PermissionService.load_principal` against a real DB.

Split out from `test_permission_service.py` (BUG-020) so the
in-memory `can()` / `assert_can()` tests don't conflate with the
DB-backed `load_principal` path.

These tests require Postgres (see the `pg_session_factory` fixture
in conftest.py). They seed a tenant + user + role + permission grant
and verify the principal comes back with the right key set.
"""
from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text

from identity.service import PermissionService

DEMO_TENANT = UUID("11111111-1111-1111-1111-111111111111")
DEMO_ADMIN_USER = UUID("22222222-2222-2222-2222-222222222222")


@pytest.mark.asyncio
async def test_load_principal_returns_admin_with_all_keys(pg_session_factory) -> None:
    """The seeded admin (from migration 0103) has every permission."""
    async with pg_session_factory() as session:
        # Set the GUC so the read is scoped to the demo tenant.
        await session.execute(text(f"SET LOCAL app.tenant_id = '{DEMO_TENANT}'"))
        svc = PermissionService(session)
        principal = await svc.load_principal(
            user_id=DEMO_ADMIN_USER, tenant_id=DEMO_TENANT
        )
    assert principal is not None
    assert principal.user_id == DEMO_ADMIN_USER
    assert principal.tenant_id == DEMO_TENANT
    # The admin has every permission from the catalog. Catalog has
    # 65 entries (per BUGS.md); the assertion is non-empty and
    # contains the well-known keys.
    keys = principal.permission_keys or set()
    assert "identity.role.read" in keys
    assert "o2c.so.write" in keys
    assert "finance.period.close" in keys
    # Service-account flag is false for the demo admin (BUG-006
    # closes the "all perms" loophole).
    assert principal.is_service_account is False
    assert principal.service_account_scopes == frozenset()


@pytest.mark.asyncio
async def test_load_principal_returns_none_for_unknown_user(pg_session_factory) -> None:
    """A bogus user_id returns None — the dep raises UnauthenticatedError."""
    async with pg_session_factory() as session:
        await session.execute(text(f"SET LOCAL app.tenant_id = '{DEMO_TENANT}'"))
        svc = PermissionService(session)
        principal = await svc.load_principal(
            user_id=UUID("00000000-0000-0000-0000-000000000000"),
            tenant_id=DEMO_TENANT,
        )
    assert principal is None


@pytest.mark.asyncio
async def test_load_principal_service_account_with_empty_scopes_cannot(
    pg_session_factory,
) -> None:
    """BUG-006: a service account with empty `service_account_scopes`
    is denied every permission.

    We construct a Principal in-process (the W0 schema doesn't have a
    service-account scopes column yet — that lands in W6) and verify
    `can()` denies everything.
    """
    from identity.service import Principal, PermissionService

    p = Principal(
        user_id=UUID("99999999-9999-9999-9999-999999999999"),
        tenant_id=DEMO_TENANT,
        is_service_account=True,
        permission_keys=frozenset(),  # irrelevant; service account bypass is closed
        service_account_scopes=frozenset(),  # empty allow-list -> deny
    )
    svc = PermissionService(session=None)  # type: ignore[arg-type]  # can() is pure
    assert await svc.can(p, "identity.role.read") is False
    assert await svc.can(p, "anything") is False
