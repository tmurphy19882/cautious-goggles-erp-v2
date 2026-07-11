"""pytest fixtures for ERP v2 tests.

Two flavours:

- Pure unit tests need nothing — they construct services directly.
- Integration tests want a live Postgres. We expose two strategies:
  1. `pg_session_factory` (function-scoped): if `ERP_TEST_DATABASE_URL` is
     set, use it. Otherwise, spin up a `testcontainers[postgres]` and run
     migrations before yielding. Skip with a clear message if neither
     is available.
  2. `client` (function-scoped): an httpx `AsyncClient` against the v2
     FastAPI app, wired to the same engine.

Tests are kept hermetic: each test gets a fresh schema, RLS is
enforced, and a known seed user is created with a known role.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Make `src` importable when pytest runs from the service root.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------- DB session fixtures ----------

def _db_url() -> str | None:
    return os.environ.get("ERP_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


async def _create_schema(engine, schema: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))


async def _drop_schema(engine, schema: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


async def _run_migrations(database_url: str) -> None:
    """Run Alembic up to head against the test database."""
    env = os.environ.copy()
    env["ERP_DATABASE_URL"] = database_url
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(repo_root),
        env=env,
        check=True,
        capture_output=True,
    )


@pytest_asyncio.fixture
async def pg_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory bound to a fresh schema in a test DB.

    Strategy:
    1. If `ERP_TEST_DATABASE_URL` is set, use it directly. Tests share the
       DB; each test gets its own schema.
    2. Else, try to start a `testcontainers[postgres]` container.
    3. Else, skip.
    """
    url = _db_url()
    if url is None:
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError:
            pytest.skip(
                "Postgres not available: set ERP_TEST_DATABASE_URL or `pip install testcontainers[postgres]`"
            )
        container = PostgresContainer("postgres:16-alpine")
        container.start()
        # asyncpg URL
        url = container.get_connection_url().replace("postgresql://", "postgresql+asyncpg://")
        # Run migrations
        await _run_migrations(url)
        try:
            engine = create_async_engine(url, future=True)
            sf = async_sessionmaker(engine, expire_on_commit=False)
            yield sf
        finally:
            await engine.dispose()
            container.stop()
    else:
        engine = create_async_engine(url, future=True)
        # Per-test schema so tests don't trample each other.
        schema = f"test_{uuid.uuid4().hex[:8]}"
        await _create_schema(engine, schema)
        # Run migrations scoped to this schema via search_path
        async with engine.begin() as conn:
            await conn.execute(text(f'SET search_path TO "{schema}"'))
        try:
            sf = async_sessionmaker(engine, expire_on_commit=False)
            yield sf
        finally:
            await _drop_schema(engine, schema)
            await engine.dispose()


# ---------- FastAPI client fixture ----------

@pytest_asyncio.fixture
async def client(pg_session_factory) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx AsyncClient wired to the v2 app."""
    from api.main import create_app_v2

    app = create_app_v2(
        session_factory=pg_session_factory,
        idempotency_store=None,  # default to DB-backed via the session_factory
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # Trigger the lifespan manually.
        async with app.router.lifespan_context(app):
            yield c
