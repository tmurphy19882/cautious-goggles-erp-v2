"""Prometheus metrics for ERP v2.

Centralised metric definitions. Every metric is a class attribute of
`Metrics` so the rest of the codebase imports one object and stays
type-safe.

Exposed metrics:

- `http_requests_total` (counter, labels: method, route, status, tenant)
- `http_request_latency_seconds` (histogram, same labels minus status)
- `outbox_events_published_total` (counter, labels: topic, result)
- `outbox_publish_latency_seconds` (histogram, labels: topic)
- `idempotency_hits_total` (counter, labels: route)
- `permission_denials_total` (counter, labels: permission_key, route)
- `rls_blocks_total` (counter, labels: table) — set when an RLS policy
  rejects a row (we sample via a `BEFORE` trigger in W10+)
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

DEFAULT_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


class Metrics:
    """Container for all ERP v2 metrics. Use the module-level `METRICS`."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.http_requests_total = Counter(
            "http_requests_total",
            "HTTP requests handled, labelled by method/route/status/tenant",
            labelnames=("method", "route", "status", "tenant"),
            registry=registry,
        )
        self.http_request_latency_seconds = Histogram(
            "http_request_latency_seconds",
            "HTTP request latency in seconds",
            labelnames=("method", "route", "tenant"),
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        )
        self.outbox_events_published_total = Counter(
            "outbox_events_published_total",
            "Outbox events published to Kafka, by topic and result",
            labelnames=("topic", "result"),  # result=ok|error
            registry=registry,
        )
        self.outbox_publish_latency_seconds = Histogram(
            "outbox_publish_latency_seconds",
            "Outbox publish latency (created_at → published_at)",
            labelnames=("topic",),
            buckets=DEFAULT_BUCKETS,
            registry=registry,
        )
        self.idempotency_hits_total = Counter(
            "idempotency_hits_total",
            "Requests served from the idempotency store (replay)",
            labelnames=("route",),
            registry=registry,
        )
        self.permission_denials_total = Counter(
            "permission_denials_total",
            "Permission denials from `PermissionService.assert_can`",
            labelnames=("permission_key", "route"),
            registry=registry,
        )
        self.rls_blocks_total = Counter(
            "rls_blocks_total",
            "Rows blocked by RLS policies",
            labelnames=("table",),
            registry=registry,
        )


_default: Metrics | None = None


def init_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    """Idempotent init. Returns the process-wide `Metrics` instance."""
    global _default
    if _default is None:
        _default = Metrics(registry=registry)
    return _default


def metrics() -> Metrics:
    """Get the singleton, initialising it if needed."""
    if _default is None:
        return init_metrics()
    return _default
