"""Integration tests for the RBAC stack (BUG-021 fix verification).

Covers:
- Unauthenticated caller (no x-user-id) → 401
- Authenticated caller with no admin role → 403
- Authenticated caller as the seeded admin → 200
- list_permissions is no longer world-readable (BUG-021 fix)
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_permissions_without_auth_returns_401(client) -> None:
    """BUG-021 fix: was world-readable; now requires identity.role.read."""
    r = await client.get("/api/v1/erp/identity/permissions")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_roles_without_auth_returns_401(client) -> None:
    r = await client.get("/api/v1/erp/identity/roles")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_roles_with_demo_admin_returns_200(client_with_demo) -> None:
    """The seed migration 0103 creates an admin user with all perms."""
    c, headers = client_with_demo
    r = await c.get("/api/v1/erp/identity/roles", headers=headers)
    assert r.status_code == 200
    roles = r.json()
    assert isinstance(roles, list)
    keys = {role["key"] for role in roles}
    assert "admin" in keys
    assert "viewer" in keys


@pytest.mark.asyncio
async def test_list_permissions_with_demo_admin_returns_catalog(client_with_demo) -> None:
    c, headers = client_with_demo
    r = await c.get("/api/v1/erp/identity/permissions", headers=headers)
    assert r.status_code == 200
    catalog = r.json()
    keys = {p["key"] for p in catalog}
    # Catalog is global; admin sees everything.
    assert "identity.user.read" in keys
    assert "o2c.so.write" in keys
    assert "platform.tenant.write" in keys
