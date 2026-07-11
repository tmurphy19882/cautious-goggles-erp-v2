"""Health and readiness endpoints for ERP v2.

- `GET /health` — liveness. Returns 200 if the process is alive.
  Excluded from the observability middleware (no trace, no metrics, no
  tenant resolution). Cheap to call from k8s liveness probes.

- `GET /ready` — readiness. Pings the DB, checks that the outbox table
  is reachable, and (W1+) reports Kafka producer status. Returns 503
  with a JSON body describing which dependency is unhealthy so the
  orchestrator (k8s, ECS, Nomad) can decide what to do.
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
    """Return 200 only if every critical dependency is reachable.

    Always checks the DB. Outbox poller and Kafka producer status are
    reported as informational fields in W0; they become hard gates in
    W1 once both are wired in production paths.
    """
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

    # Outbox table (informational until W1 publishes)
    checks["outbox"] = {"status": "ok", "note": "no outbox poller yet (W0)"}

    # Kafka producer (informational until W1)
    checks["kafka_producer"] = {"status": "ok", "note": "no producer yet (W0)"}

    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if overall_ok else "degraded", "checks": checks}


@router.get("/metrics", include_in_schema=False, summary="Prometheus metrics")
async def prometheus_metrics() -> Response:
    """Expose the Prometheus metrics registry."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
