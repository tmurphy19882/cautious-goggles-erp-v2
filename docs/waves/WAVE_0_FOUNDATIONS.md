# Wave 0 — Foundations

> **Branch:** `feat/erp-v2-w0-foundations`
> **Source:** `cautious-goggles-erp-v2` (this repo)
> **Target (parent):** `cautious-goggles/feat/erp-crm`
> **Spec:** [`/docs/SPEC.md#wave-0`](../SPEC.md)
> **Audit items closed:** see punch-list in [`/AUDIT.md`](../AUDIT.md)

W0 ships **no business behaviour** — it stands up the cross-cutting
infrastructure every later wave will rely on. The first behaviour ships
in W1 (O2C complete).

## What's in this wave

### Migrations (Alembic)
- `migrations/env.py` — async Alembic env; reads `ERP_DATABASE_URL` (or
  `DATABASE_URL`); pulls metadata from `shared.db.Base`.
- `migrations/versions/0100_identity.py` — `users`, `roles`,
  `permissions`, `role_permissions`, `user_roles` tables + the global
  permission catalog (~65 entries spanning every module in the SPEC).
- `migrations/versions/0101_identity_rls.py` — FORCE RLS on every
  tenant-scoped identity table; policies compare `tenant_id` to the
  per-request `app.tenant_id` GUC.

### `src/shared/`
- `errors.py` — `ErrorEnvelope`, `ApiError` hierarchy, FastAPI handlers.
- `schemas.py` — `AppModel` (forbid extras, from_attributes), `TenantBoundModel`,
  `MoneyDecimal` (Decimal-only, ISO 4217 currency), `PageRequest/Response`,
  `utcnow`.
- `db.py` — async engine, session factory, `Base`, `set_tenant_context`
  (`SET LOCAL app.tenant_id`), `session_scope` context manager.
- `idempotency.py` — `IdempotencyRecord`, `IdempotencyStore` interface,
  `DbIdempotencyStore` (Postgres + SQLite), `IdempotencyMiddleware`
  enforcing `Idempotency-Key` on all mutating routes with replay.
- `tenant.py` — `current_tenant_id(request)`, `require_tenant_id` dep.
- `time.py` — `utcnow`, `to_utc`.

### `src/observability/`
- `tracing.py` — idempotent OTel `TracerProvider` init with OTLP gRPC
  exporter; honours `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `metrics.py` — `Metrics` class: `http_requests_total`,
  `http_request_latency_seconds`, `outbox_events_published_total`,
  `outbox_publish_latency_seconds`, `idempotency_hits_total`,
  `permission_denials_total`, `rls_blocks_total`.
- `logging.py` — `structlog` + stdlib logging as JSON; injects
  `trace_id`, `span_id`, `tenant_id`, `request_id` via contextvars.
- `middleware.py` — `ObservabilityMiddleware`: request_id, OTel span,
  per-tenant + per-route metrics, log context wiring.

### `src/identity/`
- `models.py` — `User`, `Role`, `Permission`, `RolePermission`, `UserRole`
  SQLAlchemy 2.0 async models.
- `schemas.py` — Pydantic v2 read/write models.
- `service.py` — `Principal`, `PermissionService.assert_can/can`,
  `grant_to_role`, `load_principal`.
- `deps.py` — `get_current_principal` (W0 reads `x-user-id` header; W1
  swaps to JWT), `require_permission(key)` dep factory.
- `api.py` — `GET /api/v1/erp/identity/permissions` (global catalog) and
  `GET /api/v1/erp/identity/roles` (per-tenant, RBAC-gated).

### `src/api/`
- `health.py` — `/health` (liveness, no DB), `/ready` (DB ping + 503 on
  failure), `/metrics` (Prometheus).
- `main.py` — `create_app_v2()` factory; wires error handlers,
  idempotency middleware, observability middleware, per-module routers.
- `app.py` (root) — re-exports `create_app_v2()` so
  `uvicorn src.app:app` works.

### `tests/`
- `conftest.py` — `pg_session_factory` (testcontainers or
  `ERP_TEST_DATABASE_URL`, with per-test schema isolation), `client`
  (httpx ASGI client against the app).
- `unit/` — `test_errors.py`, `test_permission_service.py`,
  `test_idempotency.py`, `test_schemas.py`. No infra required.
- `integration/` — `test_health.py`, `test_rls_isolation.py`. Require
  Postgres.

### `scripts/`
- `dump_openapi.py` — write `docs/openapi/erp.json` from the running
  app. Used by CI to detect schema drift.
- `check_coverage.py` — static analysis guard: every mutating route in
  a non-system path must depend on `require_permission` or
  `require_tenant_id`. Wired as `erp-v2-check-coverage`.

### Other
- `services/erp/.env.example` — config samples (no real secrets).
- `services/erp/pyproject.toml` — full dep set, ruff + mypy + pytest
  config, optional `[dev]` extras.
- `docs/openapi/README.md` + `.gitignore` — placeholder until the
  generated `erp.json` lands in CI.

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| S-1 | No Temporal workflow runtime | (partial — Temporal wiring lands in W1/W2; W0 ships the table & envelope) |
| S-3 | No repository layer; routers reach into SQLAlchemy | Pattern: `PermissionService` |
| S-12 | No Pydantic schemas for entities | `AppModel`, `TenantBoundModel`, `MoneyDecimal` |
| S-15 | No idempotency on PATCH/DELETE | `IdempotencyMiddleware` enforces on all mutators |
| S-16 | No OpenAPI export | `scripts/dump_openapi.py` + `/metrics` |
| OPS-1 | No OTel spans | `ObservabilityMiddleware` + `init_tracing()` |
| OPS-2 | No Prometheus metrics | `Metrics` class + `/metrics` endpoint |
| OPS-4 | No readiness probe | `GET /ready` |
| RBAC-1 | No `roles` / `role_permissions` / `user_roles` tables | migration `0100_identity` |
| RBAC-2 | No `PermissionService` | `identity.service.PermissionService` |
| RBAC-5 | No audit log read API | (table only in W0; read API in W6) |

## What this wave does NOT do

- **No business logic.** No sales order, no party, no product, no
  invoice. The first behaviour is W1 (O2C complete).
- **No JWT.** W0 trusts the `x-user-id` header. W1 swaps to a real
  validator (PyJWT + Keycloak JWKS).
- **No outbox poller / Kafka producer.** The DB and `idempotency_keys`
  table are ready; the poller and producer land in W1.
- **No per-tenant role seeding.** The catalog is global; per-tenant
  roles are created at tenant-onboarding time in W6.

## How to run locally

```bash
cd services/erp
pip install -e ".[dev]"

# Point at a Postgres (any 14+; 16 recommended)
export ERP_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_db

# Apply migrations
alembic upgrade head

# Boot the app
uvicorn src.app:app --reload --port 8001

# Health
curl http://localhost:8001/health
# {"status":"ok","service":"erp-v2"}

curl http://localhost:8001/api/v1/erp/identity/permissions
# [... 65+ permission entries ...]

# Run tests
pytest -m unit                                   # no infra
ERP_TEST_DATABASE_URL=... pytest -m integration  # needs Postgres
```

## How to merge back to the parent

1. Push this branch to the parent repo's remote under
   `feat/erp-v2-w0-foundations`.
2. Open a PR against `cautious-goggles/feat/erp-crm` titled
   `chore(erp-v2): foundations (schemas, RBAC, OTel, idempotency, OpenAPI)`.
3. Use the parent-PR template at
   [`/MERGE_PLAN.md#per-wave-pr-template`](../MERGE_PLAN.md).
4. Once green, this branch merges to `main` here, and the parent PR
   merges to `feat/erp-crm`. W1 work starts on a fresh
   `feat/erp-v2-w1-o2c-complete` branch.
