"""Health and readiness endpoints for ERP v2.

- `GET /health` — liveness. Returns 200 if the process is alive.
  Excluded from the observability middleware (no trace, no metrics, no
  tenant resolution). Cheap to call from k8s liveness probes.

- `GET /ready` — readiness. Pings the DB and checks that the outbox
  table exists (W1+ actually publishes; we just verify the table is
  there in W0 so the orchestrator's readiness probe reports degraded
  before traffic is routed). Returns 503 with a JSON body describing
  which dependency is unhealthy.

Closes BUG-004 (partial): the outbox check actually runs `to_regclass`
now instead of being a hard-coded "ok" string. The check is still
informational in W0 (we don't 503 on a missing outbox table — that
would block the W0 merge since migration 0102+ doesn't ship the table
until W1). W1 promotes it to a hard gate.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Always returns 200 if the process is up. No DB call."""
    return {"status": "ok", "service": "erp-v2"}


@router.get("/ready", summary="Readiness probe")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Return 200 only if every critical dependency is reachable."""
    checks: dict[str, dict[str, Any]] = {}
    overall_ok = True

    # DB check
    try:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            result.scalar_one()
        checks["database"] = {"status": "ok"}
    except Exception as exc:  # pragma: no cover - depends on env
        overall_ok = False
        checks["database"] = {"status": "error", "error": str(exc)}

    # BUG-004 (partial): the outbox table check now actually runs.
    # `to_regclass` returns the OID of the relation or NULL if it
    # doesn't exist. We treat a NULL result as "outbox not yet shipped"
    # (W0) and report it as informational; W1 promotes to a hard gate.
    try:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            row = (
                await session.execute(text("SELECT to_regclass('public.outbox_events')"))
            ).scalar_one()
        if row is None:
            checks["outbox"] = {
                "status": "not_present",
                "note": "outbox_events table not yet migrated (W0)",
            }
        else:
            checks["outbox"] = {"status": "ok"}
    except Exception as exc:  # pragma: no cover - depends on env
        checks["outbox"] = {"status": "error", "error": str(exc)}

    # Kafka producer (informational until W1)
    checks["kafka_producer"] = {"status": "ok", "note": "no producer yet (W0)"}

    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if overall_ok else "degraded", "checks": checks}


@router.get("/metrics", include_in_schema=False, summary="Prometheus metrics")
async def prometheus_metrics() -> Response:
    """Expose the Prometheus metrics registry."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
