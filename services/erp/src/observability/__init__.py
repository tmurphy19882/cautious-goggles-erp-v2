"""Observability: OTel tracing, Prometheus metrics, structured logging.

Three small modules, all idempotent on `init_*()`:

- `tracing.init_tracing(service_name, otlp_endpoint)` — sets up the global
  `TracerProvider` with OTLP exporter. Safe to call once per process.
- `metrics.init_metrics()` — registers the Prometheus metrics on the
  default `REGISTRY` and returns a `Metrics` helper.
- `logging.init_logging(level)` — configures `structlog` and the stdlib
  `logging` module to emit JSON logs with trace_id + tenant_id.

The FastAPI middleware that wires request_id + OTel span context into
`request.state` lives in `observability.middleware`.
"""
from observability.logging import init_logging
from observability.metrics import init_metrics, metrics, reset_metrics_for_testing
from observability.tracing import init_tracing, tracer

__all__ = [
    "init_logging",
    "init_metrics",
    "init_tracing",
    "metrics",
    "reset_metrics_for_testing",
    "tracer",
]
