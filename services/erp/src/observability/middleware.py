"""FastAPI middleware: request_id, tenant header, OTel context, metrics."""
from __future__ import annotations

import time
import uuid
from typing import Awaitable, Callable

from fastapi import Request, Response
from opentelemetry import trace
from opentelemetry.propagate import extract
from starlette.middleware.base import BaseHTTPMiddleware

from observability.logging import (
    request_id_var,
    span_id_var,
    tenant_id_var,
    trace_id_var,
)
from observability.metrics import metrics


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Per-request middleware that:

    - Sets a `request_id` (from `X-Request-Id` or generated).
    - Extracts the OTel context from incoming `traceparent` and starts a span.
    - Records the tenant id from `x-tenant-id` into `request.state` and the log
      contextvar (so the auth check later can read it; the dep also reads it).
    - Times the request and increments the Prometheus counters/histograms.
    """

    def __init__(
        self,
        app,
        *,
        service_name: str = "erp-v2",
        excluded_paths: tuple[str, ...] = ("/health", "/ready", "/metrics"),
    ) -> None:
        super().__init__(app)
        self._tracer = trace.get_tracer(service_name)
        self._excluded = excluded_paths

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Skip observability for system endpoints to avoid feedback loops and
        # to keep metrics clean.
        if request.url.path in self._excluded:
            return await call_next(request)

        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        tenant_id = request.headers.get("x-tenant-id")
        request.state.request_id = request_id
        request.state.tenant_id = tenant_id

        token_rid = request_id_var.set(request_id)
        token_tid = tenant_id_var.set(tenant_id)

        ctx = extract(dict(request.headers))
        start = time.perf_counter()
        status_code = 500
        route_label = request.url.path
        try:
            with self._tracer.start_as_current_span(
                f"{request.method} {request.url.path}",
                context=ctx,
            ) as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.target", request.url.path)
                if tenant_id:
                    span.set_attribute("tenant.id", tenant_id)
                sc = trace.get_current_span().get_span_context()
                if sc and sc.trace_id:
                    trace_id_var.set(format(sc.trace_id, "032x"))
                if sc and sc.span_id:
                    span_id_var.set(format(sc.span_id, "016x"))
                response = await call_next(request)
                status_code = response.status_code
                route_label = getattr(request.scope.get("route"), "path", request.url.path)
                span.set_attribute("http.status_code", status_code)
                return response
        finally:
            elapsed = time.perf_counter() - start
            tenant_label = tenant_id or "anonymous"
            m = metrics()
            m.http_requests_total.labels(
                method=request.method,
                route=route_label,
                status=str(status_code),
                tenant=tenant_label,
            ).inc()
            m.http_request_latency_seconds.labels(
                method=request.method,
                route=route_label,
                tenant=tenant_label,
            ).observe(elapsed)
            request_id_var.reset(token_rid)
            tenant_id_var.reset(token_tid)
