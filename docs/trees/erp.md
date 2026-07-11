# Tree — `services/erp/` (ERP v2)

> **Branch of record:** `feat/erp-v2-w0-foundations`
> **Wave status:** W0 done, W1+ planned
> **Spec section:** [`/docs/SPEC.md#module-map`](../SPEC.md#1-module-map)
> **Mirror of:** v1 `trees/erp.md` (parent repo), ported to v2 layout

This is the canonical file map for the v2 ERP service as it stands in this
repo. Folder names and files correspond to packages under
`services/erp/src/`. Modules marked **planned** are seeded in the source
tree (so the package layout is reviewable now) but ship in the named
later wave; see the column for which wave fills them in.

## Top-level layout

```
services/erp/
├── README.md                        # service-level pointer
├── pyproject.toml                   # deps, ruff, mypy, pytest
├── .env.example                     # config samples
├── migrations/
│   ├── alembic.ini
│   ├── env.py                       # async Alembic env
│   ├── script.py.mako
│   └── versions/
│       ├── 0100_identity.py         # users / roles / permissions + global catalog
│       └── 0101_identity_rls.py     # FORCE RLS on tenant-scoped identity tables
├── scripts/
│   ├── dump_openapi.py              # writes docs/openapi/erp.json
│   └── check_coverage.py            # static guard: every mutator gated
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_errors.py
│   │   ├── test_schemas.py
│   │   ├── test_idempotency.py
│   │   └── test_permission_service.py
│   ├── integration/
│   │   ├── test_health.py
│   │   └── test_rls_isolation.py
│   └── contract/                    # planned — pact tests in W10
└── src/
    ├── app.py                       # uvicorn entry: re-exports create_app_v2()
    ├── api/
    │   ├── main.py                  # FastAPI factory
    │   └── health.py                # /health /ready /metrics
    ├── shared/                      # cross-cutting (no domain code)
    ├── observability/               # OTel, Prometheus, structured logging
    └── <module>/                    # one Python package per SPEC module
```

## `src/shared/` — cross-cutting, used by every module

| File | Purpose | Wave |
|------|---------|------|
| `__init__.py` | Re-exports the public surface | W0 |
| `errors.py` | `ErrorEnvelope`, `ApiError` hierarchy, FastAPI handlers | W0 |
| `schemas.py` | Pydantic v2 base: `AppModel`, `TenantBoundModel`, `MoneyDecimal`, `PageRequest/Response`, `utcnow` | W0 |
| `db.py` | Async engine, session factory, `Base`, `set_tenant_context` (RLS GUC) | W0 |
| `idempotency.py` | `IdempotencyRecord`, `IdempotencyStore`, `DbIdempotencyStore`, `IdempotencyMiddleware` | W0 |
| `tenant.py` | `current_tenant_id(request)`, `require_tenant_id` dep | W0 |
| `time.py` | `utcnow`, `to_utc` | W0 |
| `outbox.py` | `OutboxEvent`, `OutboxStore` (table + repo) | W1 |
| `events.py` | `EventEnvelope` for Apicurio-validated payloads | W1 |
| `feature_flags.py` | Unleash client wrapper, per-tenant override | W7 |
| `audit.py` | Audit log writer, `audit_log` table | W0 (table), W6 (read API) |

> **Import rule (W0):** import submodules directly — `from shared.errors
> import ApiError`. The package re-exports the most common symbols but
> not everything; if a symbol isn't re-exported, import from the
> submodule.

## `src/observability/`

| File | Purpose | Wave |
|------|---------|------|
| `tracing.py` | OTel `TracerProvider` init + OTLP gRPC exporter | W0 |
| `metrics.py` | `Metrics` class (Prometheus registry, all counters/histograms) | W0 |
| `logging.py` | `structlog` + stdlib logging as JSON; contextvars for trace/tenant | W0 |
| `middleware.py` | `ObservabilityMiddleware` (request_id, OTel span, per-tenant metrics) | W0 |

## `src/identity/` — RBAC

| File | Purpose | Wave |
|------|---------|------|
| `models.py` | `User`, `Role`, `Permission`, `RolePermission`, `UserRole` | W0 |
| `schemas.py` | `PermissionRead`, `RoleRead`, `RoleWrite` (Pydantic v2) | W0 |
| `service.py` | `Principal`, `PermissionService.{assert_can,can,load_principal,grant_to_role}` | W0 |
| `deps.py` | `get_current_principal`, `require_permission(key)` dep factory | W0 |
| `api.py` | `GET /identity/permissions`, `GET /identity/roles`, `GET /identity/roles/{id}` | W0 (read), W6 (write) |
| `provision.py` | `provision_erp_tenant(tenant_id)` — seeds default roles | W6 |

## Module map (per [`SPEC.md`](../SPEC.md#1-module-map))

Every row is a Python package under `services/erp/src/`. The
**shipped** column indicates the wave that lands the first behaviour in
that module; the **stub** column lists what is there now.

| Module | Sub-packages (planned) | First wave | W0 stub |
|--------|------------------------|------------|---------|
| `identity/` | — | W0 (table + service + read API) | filled in W0 |
| `master-data/party/` | `customer`, `vendor`, `carrier`, `internal_org`, `employee` | W3 | empty package |
| `master-data/product/` | `part`, `bom`, `uom` | W3 | empty package |
| `master-data/location/` | `warehouse`, `plant`, `address` | W3 | empty package |
| `master-data/pricing/` | `price_list`, `contract`, `discount` | W3 | empty package |
| `master-data/import/` | `csv`, `xlsx` | W3 | empty package |
| `master-data/search/` | — | W3 | empty package |
| `o2c/sales-order/` | `line`, `validator`, `lifecycle` | W1 | empty package |
| `o2c/credit/` | `limit`, `hold`, `release` | W1 | empty package |
| `o2c/reservation/` | `project`, `release` | W1 | empty package |
| `o2c/invoice/` | `ar_invoice`, `ar_aging` | W1 | empty package |
| `o2c/payment/` | `application`, `refund`, `writeoff` | W1 | empty package |
| `o2c/revenue-recognition/` | `rules`, `scheduler` | W1 | empty package |
| `o2c/cogs/` | `fifo`, `inventory_valuation` | W1 | empty package |
| `p2p/requisition/` | `consumer` (MRP) | W2 | empty package |
| `p2p/purchase-order/` | `lifecycle`, `approval` | W2 | empty package |
| `p2p/receipt/` | `consumer` (WMS) | W2 | empty package |
| `p2p/three-way-match/` | `tolerance`, `hold` | W2 | empty package |
| `p2p/ap/` | `ap_invoice`, `ap_aging` | W2 | empty package |
| `finance/chart-of-accounts/` | `seed`, `coa_tree` | W4 | empty package |
| `finance/journal/` | `manual`, `auto_post` | W4 | empty package |
| `finance/period/` | `close`, `lock` | W4 | empty package |
| `finance/tax/` | `engine`, `exemption`, `nexus` | W1 (engine) | empty package |
| `finance/fx/` | `rate`, `revaluation` | W1 (rate) | empty package |
| `finance/aging/` | `ar`, `ap` | W4 | empty package |
| `crm/contact/` | `sub_party` | W5 | empty package |
| `crm/lead/` | `qualify`, `convert` | W5 | empty package |
| `crm/pipeline/` | `stage`, `kanban` | W5 | empty package |
| `crm/opportunity/` | `lifecycle`, `forecast` | W5 | empty package |
| `crm/quote/` | `lines`, `convert_to_so` | W5 | empty package |
| `crm/contract/` | `clauses` | W5 | empty package |
| `crm/activity/` | `timeline` | W5 | empty package |
| `crm/ticket/` | `sla`, `comments` | W5 | empty package |
| `crm/ai-coach/` | `suggest`, `rag` | W7 | empty package |
| `crm/notification/` | `in_app`, `email`, `sms` | W7 | empty package |
| `crm/saved-view/` | `per_role` | W7 | empty package |
| `crm/custom-field/` | `per_tenant_schema` | W7 | empty package |
| `hr/` | `employee`, `department`, `org`, `payroll_export` | W8 | empty package |
| `legal/` | `contract`, `clause`, `approval`, `docusign` | W8 | empty package |
| `trade/hts/` | `engine`, `auto_resolve` | W1 (auto-resolve), W9 (polish) | empty package |
| `trade/ftz/` | `admit`, `remove`, `deferred_duty` | W9 | empty package |
| `trade/screening/` | `rescreen`, `employee`, `internal_org` | W9 | empty package |
| `trade/customs/` | `state_machine` | W9 | empty package |
| `platform/tenant-onboarding/` | `realm`, `db_provision`, `consumer_register` | W6 | empty package |
| `platform/connector/` | `oauth`, `sync`, `last_sync_at` | W6 | empty package |
| `platform/webhook/` | `outbound`, `inbound` | W6 | empty package |
| `platform/import-export/` | `csv`, `xlsx` | W6 | empty package |
| `platform/payment-gateway/` | `stripe`, `ach` | W6 | empty package |
| `ai/registry/` | `agent_client` | W7 | empty package |
| `ai/rag/` | `chunks`, `embeddings`, `vector` | W7 | empty package |

## Conventions

- **Every module is a Python package** under `services/erp/src/`. A
  module is a package; a sub-module is a sub-package.
- **Each module follows the inner layout** `domain/`, `infrastructure/`,
  `workflows/`, `api.py`, `service.py`, `models.py`, `schemas.py`
  (per [`ADR-0010`](../adrs/0010-domain-infrastructure-split.md)).
  W0's `identity/` is the reference implementation.
- **The router is `api.py`** inside the module. `api/main.py` does *not*
  import module routers; each wave's PR adds the `app.include_router`
  call inside the module so an unfinished wave never blocks boot.
- **No bare domain code in `shared/`.** `shared/` is for the
  cross-cutting concerns only. Anything specific to a module lives in
  that module's package.

## What changed vs v1

| Area | v1 (`cautious-goggles/services/erp/`) | v2 (this repo) |
|------|---------------------------------------|----------------|
| Routing | `src/api/{orders,parties,trade,connectors}.py` mounted in `app.py` | Module-local `api.py` per package; factory in `src/api/main.py` |
| Schema validation | Mix of Pydantic v1 + hand-rolled dicts | Pydantic v2 only, via `shared.schemas.AppModel` |
| ORM | SQLAlchemy 2.0 sync | SQLAlchemy 2.0 async |
| DB driver | `psycopg2` | `asyncpg` |
| Migrations | Alembic 1.12 (sync env) | Alembic 1.13 async env |
| Outbox | In-process pub/sub | DB `outbox_events` + poller (W1) |
| RBAC | None | `PermissionService` + global catalog |
| Idempotency | None | `IdempotencyMiddleware` on every mutator |
| OTel | None | First-class, every handler |
| OpenAPI | Generated ad-hoc by FastAPI | Generated, dumped, CI diff'd |
