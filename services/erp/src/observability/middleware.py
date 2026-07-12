"""FastAPI middleware: request_id, tenant header, OTel context, metrics.

Closes:

- **BUG-007** — resolve the tracer *per-request* (and call `init_tracing()`
  in `__init__` so the provider is set before any request hits) instead of
  caching a proxy tracer in `__init__` before the lifespan runs.
- **BUG-022** — go through the project's `observability.tracing.tracer()`
  wrapper instead of calling `opentelemetry.trace.get_tracer` directly.
- **BUG-016** — `trace_id_var` and `span_id_var` are set with `set()` and
  reset with `reset()` in `finally` so they don't leak between requests.
- **BUG-008** — the middleware parses `x-tenant-id` to a `UUID | None` and
  stores that on `request.state.tenant_id`. Downstream readers (the tenant
  dep, idempotency middleware) can now trust the type.
"""
from __future__ import annotations

import time
import uuid
from contextvars import Token
from typing import Awaitable, Callable
from uuid import UUID

from fastapi import Request, Response
from opentelemetry.propagate import extract
from starlette.middleware.base import BaseHTTPMiddleware

from observability.logging import (
    request_id_var,
    span_id_var,
    tenant_id_var,
    trace_id_var,
)
from observability.metrics import metrics
from observability.tracing import init_tracing, tracer


def _parse_tenant(raw: str | None) -> UUID | None:
    """Parse `x-tenant-id` to a UUID. Returns None if missing or malformed."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except (ValueError, AttributeError):
        return None


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Per-request middleware that:

    - Sets a `request_id` (from `X-Request-Id` or generated).
    - Extracts the OTel context from incoming `traceparent` and starts a span.
    - Parses `x-tenant-id` to a `UUID` and stores it on `request.state`
      (the tenant dep + idempotency middleware read it from there).
    - Times the request and increments the Prometheus counters/histograms,
      even on exceptions (the metric increment lives in `finally`).
    """

    def __init__(
        self,
        app,
        *,
        service_name: str = "erp-v2",
        excluded_paths: tuple[str, ...] = ("/health", "/ready", "/metrics"),
    ) -> None:
        super().__init__(app)
        # BUG-007: call init_tracing in __init__ so the provider is set
        # before any request hits — `add_middleware` runs at app-construction
        # time, before the lifespan fires. Per-request resolution still
        # happens in `dispatch` to pick up the live provider.
        init_tracing(service_name=service_name)
        self._service_name = service_name
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
        raw_tenant = request.headers.get("x-tenant-id")
        # BUG-008: parse to UUID once. Downstream readers (the tenant dep,
        # idempotency middleware) get a real `UUID` instead of a raw string
        # they have to coerce.
        tenant_id = _parse_tenant(raw_tenant)
        request.state.request_id = request_id
        request.state.tenant_id = tenant_id
        # The log contextvar still stores the string form (structured logs
        # serialise it that way); the dep / middleware read `request.state`
        # for the typed value.
        tenant_id_str_for_log = raw_tenant or ""

        token_rid = request_id_var.set(request_id)
        token_tid = tenant_id_var.set(tenant_id_str_for_log)
        token_trace: Token[str | None] | None = None
        token_span: Token[str | None] | None = None

        ctx = extract(dict(request.headers))
        start = time.perf_counter()
        status_code = 500
        route_label = request.url.path
        try:
            # BUG-022: go through the project's tracer() wrapper, not
            # `opentelemetry.trace.get_tracer` directly. The wrapper
            # returns a tracer bound to the live provider (set by
            # `init_tracing` in __init__ above).
            with tracer(self._service_name).start_as_current_span(
                f"{request.method} {request.url.path}",
                context=ctx,
            ) as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.target", request.url.path)
                if tenant_id_str_for_log:
                    span.set_attribute("tenant.id", tenant_id_str_for_log)
                from opentelemetry import trace as _ot_trace  # local import; only used to read span ctx
                sc = _ot_trace.get_current_span().get_span_context()
                if sc and sc.trace_id:
                    token_trace = trace_id_var.set(format(sc.trace_id, "032x"))
                if sc and sc.span_id:
                    token_span = span_id_var.set(format(sc.span_id, "016x"))
                response = await call_next(request)
                status_code = response.status_code
                route_label = getattr(request.scope.get("route"), "path", request.url.path)
                span.set_attribute("http.status_code", status_code)
                return response
        finally:
            # Metric increment in `finally` so exceptions still record
            # the request (with status 500) instead of dropping the metric.
            elapsed = time.perf_counter() - start
            tenant_label = tenant_id_str_for_log or "anonymous"
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
            if token_trace is not None:
                trace_id_var.reset(token_trace)
            if token_span is not None:
                span_id_var.reset(token_span)
