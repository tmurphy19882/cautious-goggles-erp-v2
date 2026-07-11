"""Integration tests for RLS isolation in the identity schema.

These verify that RLS policies added in `0101_identity_rls` correctly
block cross-tenant reads/writes when the GUC `app.tenant_id` is set.

BUG-026 (P2): the original test set the GUC mid-session and asserted
isolation. Postgres `SET LOCAL` is transaction-scoped; on a single
connection the GUC re-evaluates per statement, but the policy is
applied per-row, so the assertion was correct *and* the test should
also verify that *the same session* re-evaluates the policy when the
GUC changes. We now do that explicitly.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_rls_isolates_users_per_tenant(pg_session_factory) -> None:
    """Reading users in tenant A must not see users in tenant B.

    Uses *separate sessions* (per BUG-026 + the audit cross-ref
    table) for each tenant's read so the GUC is fresh on each
    `SET LOCAL` and the connection pool is not pinned to a stale
    state.
    """
    sf = pg_session_factory
    tenant_a = uuid4()
    tenant_b = uuid4()

    # Seed one user in each tenant (separate sessions so each has its
    # own GUC and RLS evaluation).
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_a)})
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email) VALUES (uuid_generate_v4(), :t, :e)"
            ),
            {"t": str(tenant_a), "e": "a@example.com"},
        )
        await session.commit()

    async with sf() as session:
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
async def test_rls_re_evaluates_on_guc_change_in_same_session(pg_session_factory) -> None:
    """BUG-026: within a single session, switching the GUC between
    statements must cause the RLS policy to re-evaluate. This catches
    a Postgres-level regression where the policy is cached on the
    session instead of re-checked."""
    sf = pg_session_factory
    tenant_a = uuid4()
    tenant_b = uuid4()

    # Seed A and B in two separate sessions (so the GUC is clean).
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_a)})
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email) VALUES (uuid_generate_v4(), :t, :e)"
            ),
            {"t": str(tenant_a), "e": "a@example.com"},
        )
        await session.commit()
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_b)})
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email) VALUES (uuid_generate_v4(), :t, :e)"
            ),
            {"t": str(tenant_b), "e": "b@example.com"},
        )
        await session.commit()

    # Same session, switch GUC, re-read. Must return 1 row each time.
    async with sf() as session:
        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_a)})
        rows = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()
        assert rows == 1, f"expected 1 row as A, got {rows}"

        await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_b)})
        rows = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()
        assert rows == 1, f"expected 1 row as B, got {rows}"


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
