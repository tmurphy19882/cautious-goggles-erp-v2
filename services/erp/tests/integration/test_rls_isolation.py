"""Integration tests for RLS isolation in the identity schema.

These verify that RLS policies added in `0101_identity_rls` correctly
block cross-tenant reads/writes when the GUC `app.tenant_id` is set.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text


@pytest.mark.asyncio
async def test_rls_isolates_users_per_tenant(pg_session_factory) -> None:
    """Reading users in tenant A must not see users in tenant B."""
    sf = pg_session_factory
    tenant_a = uuid4()
    tenant_b = uuid4()

    # Seed one user in each tenant.
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_a)})
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email) VALUES (uuid_generate_v4(), :t, :e)"
            ),
            {"t": str(tenant_a), "e": "a@example.com"},
        )
        # Same connection — switch tenant to B and insert.
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_b)})
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email) VALUES (uuid_generate_v4(), :t, :e)"
            ),
            {"t": str(tenant_b), "e": "b@example.com"},
        )
        await session.commit()

    # Now read as tenant A — should see only A's user.
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_a)})
        rows = (await session.execute(text("SELECT email FROM users"))).scalars().all()
        assert rows == ["a@example.com"]

    # Read as tenant B — should see only B's user.
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_b)})
        rows = (await session.execute(text("SELECT email FROM users"))).scalars().all()
        assert rows == ["b@example.com"]


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_insert(pg_session_factory) -> None:
    """Inserting with a `tenant_id` different from the GUC must fail."""
    sf = pg_session_factory
    declared_tenant = uuid4()
    other_tenant = uuid4()

    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(declared_tenant)})
        # The CHECK on the RLS policy should reject this row because
        # `tenant_id` doesn't match the GUC.
        with pytest.raises(Exception):
            await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email) VALUES (uuid_generate_v4(), :t, :e)"
                ),
                {"t": str(other_tenant), "e": "bad@example.com"},
            )
        await session.rollback()
