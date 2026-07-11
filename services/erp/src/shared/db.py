"""Database engine, session factory, Base, and tenant context for ERP v2.

Single SQLAlchemy 2.0 async engine per process. Sessions are obtained via
`async_sessionmaker`; RLS is enforced via the `app.tenant_id` GUC set per
request by `set_tenant_context` (called inside each request middleware).

Per ADR-0007 (inherited from parent), RLS is the *only* way tenant data
is partitioned. Application code never sees `erp_user` privileges that
bypass RLS.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# Naming convention = predictable constraint names = easy migrations.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


def database_url() -> str:
    """Resolve the DB URL from env, with a safe local default."""
    return os.environ.get("ERP_DATABASE_URL") or os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://erp:erp@localhost:5432/erp_db",
    )


def create_engine(url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """Create the async engine. Tests can pass a custom URL."""
    url = url or database_url()
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
        **kwargs,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )


async def set_tenant_context(session: AsyncSession, tenant_id: UUID | str | None) -> None:
    """Set the per-transaction `app.tenant_id` GUC consumed by RLS policies.

    Call this at the start of every request's DB session. Use `SET LOCAL`
    so the setting is transaction-scoped and never leaks across pooled
    connections.

    Closes BUG-003: passing `tenant_id=None` used to set the GUC to the
    empty string, which the RLS policy then tried to cast as `::uuid` —
    Postgres raised `invalid input syntax for type uuid`. We now set the
    GUC to a sentinel UUID that never matches any real tenant row, so
    system paths (health, bootstrap) get zero rows back instead of an
    error.
    """
    if tenant_id is None:
        # Sentinel UUID that no real tenant will ever use. RLS policy in
        # 0101_identity_rls.py / 0102_idempotency_keys.py falls through to
        # the same sentinel via COALESCE(NULLIF(...), SENTINEL), so system
        # paths get a clean "no rows" instead of a uuid cast error.
        await session.execute(text(f"SET LOCAL app.tenant_id = '{SENTINEL_TENANT_UUID}'"))
        return
    await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_id)})


SENTINEL_TENANT_UUID = "00000000-0000-0000-0000-000000000000"


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID | str | None,
) -> AsyncIterator[AsyncSession]:
    """Open a session, set tenant context, commit on success, rollback on error."""
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
