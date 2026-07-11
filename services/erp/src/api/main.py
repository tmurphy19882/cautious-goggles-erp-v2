"""FastAPI app factory for ERP v2.

Wires:
- error envelope handlers
- observability middleware (OTel, metrics, structured logs, request_id)
- idempotency middleware
- per-module routers (identity, …)
- startup / shutdown hooks for engine + idempotency store

Run with:

    uvicorn api.main:app --reload --port 8001

Or for tests:

    from api.main import create_app_v2
    app = create_app_v2(database_url="postgresql+asyncpg://…")
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from api import health
from identity import api as identity_api
from observability import init_logging, init_metrics, init_tracing
from observability.middleware import ObservabilityMiddleware
from shared.db import Base, create_engine, create_session_factory
from shared.errors import install_error_handlers
from shared.idempotency import DbIdempotencyStore, IdempotencyMiddleware
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


def create_app_v2(
    *,
    database_url: str | None = None,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker | None = None,
    idempotency_store: DbIdempotencyStore | None = None,
    otlp_endpoint: str | None = None,
    log_level: str | None = None,
    service_name: str = "erp-v2",
) -> FastAPI:
    """Build the v2 FastAPI app.

    Wires everything needed for W0 (foundations). Later waves add their
    routers via `app.include_router(...)` inside the module that ships
    the wave; we do *not* import them here so an unfinished wave never
    blocks the W0 boot.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Touch OTel + metrics so first-request is hot.
        init_tracing(service_name=service_name, otlp_endpoint=otlp_endpoint)
        init_metrics()
        logger.info("erp-v2 starting", extra={"service": service_name, "ts": utcnow().isoformat()})
        try:
            yield
        finally:
            if engine is not None and not getattr(engine, "_test_owned", False):
                await engine.dispose()
            logger.info("erp-v2 stopped")

    app = FastAPI(
        title="ERP Service v2",
        version="0.1.0",
        description="Clean rebuild of cautious-goggles/services/erp.",
        lifespan=lifespan,
    )

    # Engine + session factory
    if engine is None:
        engine = create_engine(database_url)
    if session_factory is None:
        session_factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Idempotency store (Postgres-backed by default)
    if idempotency_store is None:
        idempotency_store = DbIdempotencyStore(session_factory)
    app.state.idempotency_store = idempotency_store

    # Observability — log first so middleware setup logs are captured.
    init_logging(level=log_level or os.environ.get("LOG_LEVEL", "INFO"))

    # Middleware order matters: observability OUTSIDE idempotency so the
    # span covers idempotency processing.
    app.add_middleware(IdempotencyMiddleware, store=idempotency_store)
    app.add_middleware(ObservabilityMiddleware, service_name=service_name)

    # Error envelope
    install_error_handlers(app)

    # Routers
    app.include_router(health.router)
    app.include_router(identity_api.router, prefix="/api/v1/erp")

    # Re-export metadata for tooling that needs it (e.g. Alembic env).
    app.state.metadata = Base.metadata

    return app
