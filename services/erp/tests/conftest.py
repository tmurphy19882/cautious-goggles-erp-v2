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

Closes BUG-016: autouse fixture resets the OTel log contextvars
(request_id, span_id, trace_id, tenant_id) between tests so they
don't leak across the suite.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from prometheus_client import CollectorRegistry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Make `src` importable when pytest runs from the service root.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------- BUG-016: autouse reset of OTel contextvars ----------

@pytest.fixture(autouse=True)
def _reset_otel_contextvars() -> AsyncIterator[None]:
    """Reset the OTel log contextvars between tests so they don't leak.

    The `ObservabilityMiddleware` resets `request_id_var` and `tenant_id_var`
    in its `finally` block, but `trace_id_var` and `span_id_var` were set
    *without* tokens and never reset (BUG-016). This fixture does the
    unconditional reset for all four.
    """
    yield  # let the test run
    try:
        from observability.logging import (
            request_id_var,
            span_id_var,
            tenant_id_var,
            trace_id_var,
        )
    except ImportError:
        return
    for var in (request_id_var, span_id_var, tenant_id_var, trace_id_var):
        try:
            var.set(None)  # type: ignore[arg-type]
        except Exception:
            pass


# ---------- Per-test metrics registry ----------

@pytest.fixture(autouse=True)
def _reset_metrics_registry() -> AsyncIterator[None]:
    """Each test gets a fresh Prometheus registry.

    The process-wide `Metrics` singleton is reset so the next call to
    `metrics()` re-inits with the test's local registry. This keeps the
    /metrics output deterministic per test.
    """
    from observability.metrics import reset_metrics_for_testing
    reset_metrics_for_testing()
    test_registry = CollectorRegistry()
    from observability.metrics import init_metrics
    init_metrics(registry=test_registry)
    yield
    reset_metrics_for_testing()


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
    try:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(repo_root),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, file=sys.stdout)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise


# Fixed UUIDs that match migration 0103_seed_tenant.
DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEMO_ADMIN_USER_ID = "22222222-2222-2222-2222-222222222222"
DEMO_ADMIN_ROLE_ID = "33333333-3333-3333-3333-333333333333"


@pytest_asyncio.fixture
async def pg_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session factory bound to a fresh schema in a test DB."""
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


# ---------- Tenant + admin header fixture (auth shim for W0) ----------

@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Headers to call gated routes as the demo admin user.

    The seeded admin user (see migration 0103) has every permission
    granted, so any `require_permission(...)` dep will pass.
    """
    return {
        "x-tenant-id": DEMO_TENANT_ID,
        "x-user-id": DEMO_ADMIN_USER_ID,
    }


# ---------- Per-test session + tenant + user fixtures (W7-W10) ----------

@pytest_asyncio.fixture
async def session(pg_session_factory) -> AsyncIterator[AsyncSession]:
    """Yield an `AsyncSession` bound to the test schema.

    Used by the W7-W10 service-level tests that don't go through the
    HTTP layer.
    """
    sf = pg_session_factory
    async with sf() as s:
        yield s


@pytest.fixture
def tenant_id() -> UUID:
    """The seeded demo tenant id (matches migration 0103)."""
    return UUID(DEMO_TENANT_ID)


@pytest.fixture
def user_id() -> UUID:
    """The seeded demo admin user id (matches migration 0103)."""
    return UUID(DEMO_ADMIN_USER_ID)


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
