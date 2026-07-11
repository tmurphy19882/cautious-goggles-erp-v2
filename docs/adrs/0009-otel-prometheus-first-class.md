# ADR-0009 — OTel + Prometheus first-class on every handler

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | W0 ships the middleware + metrics registry; W1+ adds span attributes per handler |
| Supersedes | v1's lack of structured observability (audit items OPS-1, OPS-2) |
| Related | [`SPEC.md`](../SPEC.md) |

## Context

v1 has no observability. There is no OTel tracer, no Prometheus
metrics endpoint, no structured log. An incident in v1 requires
SSH-ing to the box, reading the application's stdout, and
grepping for the request id — if the request had an id. The
"is the service up" question is answered by the load balancer's
health check, which only tells you the process is alive.

What v2 needs:

- **Per-request OTel spans** with `http.method`, `http.target`,
  `http.status_code`, and `tenant.id` attributes.
- **Per-request Prometheus metrics**:
  `http_requests_total{method, route, status, tenant}` and
  `http_request_latency_seconds{method, route, tenant}`.
- **Structured logs** as JSON, with `trace_id`, `span_id`,
  `request_id`, `tenant_id` injected via contextvars so every
  log line emitted during a request can be correlated back.
- **Per-handler span attributes** for the *interesting* work
  (e.g. SO confirm: span attribute `so.id`, `so.lines`,
  `so.tenant_id`).
- **System endpoints** (`/health`, `/ready`, `/metrics`) are
  excluded from the per-request observability path to avoid
  feedback loops in the metrics pipeline.

Options:

- **OpenTelemetry SDK direct.** Verbose; we re-implement the
  middleware in every service.
- **OpenTelemetry SDK + `opentelemetry-instrumentation-fastapi`.**
  Auto-instrumentation; gets us most of the way; we add
  custom span attributes per handler for the interesting bits.
- **Prometheus client only.** Metrics yes, traces no. The
  "which request did this log line belong to" question is
  unanswerable.
- **Datadog / New Relic agent.** SaaS-bound, paid, hard to
  develop against locally.

## Decision

- **OTel SDK + auto-instrumentation for FastAPI, SQLAlchemy,
  and asyncpg.** Auto spans cover the boilerplate; per-handler
  `span.set_attribute(...)` calls cover the interesting work.
- **OTLP gRPC exporter** to the platform's OTel collector.
  Endpoint from `OTEL_EXPORTER_OTLP_ENDPOINT`; if unset, the
  SDK is still initialised and spans are emitted to a
  no-op sink (i.e. observability is not a hard dependency
  for boot).
- **Prometheus client** for metrics. A `/metrics` endpoint
  in `api/health.py` exposes the registry. Per-tenant labels
  become a high-cardinality risk; the W10 plan throttles
  with a tenant-id bucket and a `drop_high_cardinality`
  config flag.
- **structlog** + stdlib logging as JSON.
  `ObservabilityMiddleware` sets contextvars
  (`request_id`, `tenant_id`, `trace_id`, `span_id`) at the
  start of each request and clears them on response. Every
  log line emitted during the request gets these fields
  automatically.
- **No opt-out.** Every handler runs through
  `ObservabilityMiddleware`. Handlers that want to skip
  observability must use a system path registered as
  excluded (`/health`, `/ready`, `/metrics`).

## Consequences

### Positive

- **Every request is traceable.** A `request_id` is
  correlated across the log, the trace, and the metric.
- **The middleware is one file.** Per-handler code is
  unchanged for the common case; per-handler attributes
  are added where the handler does interesting work.
- **OTel SDK + structlog are open source.** No SaaS
  dependency; local dev runs with a local collector or
  without one.
- **System endpoints are excluded.** The metrics pipeline
  doesn't see its own scrape.

### Negative / costs

- **Per-tenant labels are a cardinality risk.** W10
  monitors `prometheus_tsdb_head_series` and throttles
  to top-N tenants + `tenant=other` if needed.
- **structlog is a learning curve for new contributors.**
  The W0 boot path already calls `init_logging()`; new
  modules just import the bound logger.
- **OTLP endpoint is optional in dev.** A developer
  without an OTel collector still gets a metrics
  endpoint and structured logs; they just don't get
  traces until they `docker compose up otel-collector`.

### W0 scope (concrete)

W0 ships:

- `ObservabilityMiddleware` in
  `src/observability/middleware.py`.
- `Metrics` class in `src/observability/metrics.py` with
  `http_requests_total`, `http_request_latency_seconds`,
  `outbox_events_published_total`,
  `outbox_publish_latency_seconds`,
  `idempotency_hits_total`,
  `permission_denials_total`, `rls_blocks_total`.
- `init_tracing()`, `init_metrics()`, `init_logging()`
  in the FastAPI lifespan.
- `GET /metrics` endpoint.
- Contextvars `request_id_var`, `span_id_var`,
  `tenant_id_var`, `trace_id_var`.

W0 does **not** ship:

- OTel collector in `infra/docker-compose.yml` (deferred
  to W10).
- Per-tenant Prometheus label throttling (W10).
- The otel-collector service (out of scope for this
  service; lives in the platform's `infra/`).
