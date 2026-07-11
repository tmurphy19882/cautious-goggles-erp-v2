# Wave 0 — Foundations (DONE)

> **Branch:** `feat/erp-v2-w0-foundations`
> **HEAD:** `e2564ee` ("fix(w0): apply P0/P1 audit findings + infra + seed + working local stack")
> **Target (parent):** `cautious-goggles/feat/erp-crm`
> **Spec:** [`/docs/SPEC.md#wave-0`](../SPEC.md)
> **Audit:** [`/BUGS.md`](../../BUGS.md) — closed 7 of 8 P0 (BUG-004 deferred to W1)
> **Missing files:** [`/MISSING_FILES.md`](../../MISSING_FILES.md) — all P0 added
> **Research:** [`/docs/RESEARCH_V1_PATTERNS.md`](../RESEARCH_V1_PATTERNS.md)
> **Adopt-now:** [`/ADOPT_NOW.md`](../../ADOPT_NOW.md) — items 1, 2, 3 all adopted

W0 ships the cross-cutting infrastructure plus all P0 bug fixes from
the audit, the P1 backlog (most of it), and the missing infra + scripts
+ tests + ADRs. Ready to cherry-pick into `cautious-goggles/feat/erp-crm`.

## Commits in this wave

| SHA       | Message |
|-----------|---------|
| `fd49132` | `chore: initial erp-v2 skeleton` |
| `b193226` | `feat(erp-v2): W0 foundations` — original 39-file W0 ship |
| `6699472` | `docs(w0): bug audit + missing-files inventory` |
| `bc74999` | `docs(w0): add getting-started, development, troubleshooting, ADRs, tree docs, API changelog entries` |
| `d07d30d` | `docs(w0): tag every P0 with BEFORE-MERGE / BEFORE-W1 gate` |
| `e2564ee` | `fix(w0): apply P0/P1 audit findings + infra + seed + working local stack` |

## What ships in W0

### Cross-cutting
- `shared/` — errors (envelope), schemas (Pydantic v2), db (async engine + RLS), idempotency (replay middleware), tenant (UUID context)
- `observability/` — OTel tracing + Prometheus metrics + structlog + middleware (per-request OTel span, per-tenant+per-route metrics, contextvar wiring)
- `identity/` — User, Role, Permission, RolePermission, UserRole; `PermissionService.assert_can`; `require_permission(key)` dep
- `api/` — `/health`, `/ready` (DB + outbox check), `/metrics`, app factory

### Migrations
- `0100_identity` — users, roles, permissions, role_permissions, user_roles; ~65 permission keys seeded
- `0101_identity_rls` — FORCE RLS on every tenant-scoped identity table
- `0102_idempotency_keys` — composite PK `(tenant_id, key_hash)`, JSONB response_body, FORCE RLS
- `0103_seed_tenant` — demo admin user + admin role with every permission

### Infra (`infra/`)
- `docker-compose.yml` — two profiles: `core` (PG + Redis) and `infra` (full stack: PG + Redis + Redpanda + Apicurio + Keycloak + Kong + OTel + Unleash)
- `postgres/{docker-compose.yml, init.sql}` — per-service DB init
- `kafka/topics.yaml` — W1+ topic manifest
- `keycloak/realm-export.json` — JWT issuer with `erp-v2` client and `x-tenant-id` claim mapper
- `kong/kong.yml` — `/api/v1/erp` → `erp-v2:8001`
- `otel/collector-config.yaml` — OTLP receiver + batch + logging exporter

### Service artifacts
- `services/erp/Dockerfile` — multi-stage, non-root, healthcheck
- `services/erp/.dockerignore`
- `services/erp/Makefile` — `make install / migrate / run / test / test-unit / test-integration / seed / smoke / openapi / coverage / lint / format / typecheck / docker-build / docker-run`
- `services/erp/scripts/seed_demo.py` — idempotent seed of demo tenant
- `services/erp/scripts/smoke_test.py` — boot + run migrations + seed + curl every endpoint

### Tests (added in W0 hotfix)
- `tests/conftest.py` — autouse OTel contextvar reset, per-test metrics registry, `pg_session_factory` (testcontainers + ERP_TEST_DATABASE_URL), `admin_headers` fixture, `client` fixture
- `tests/unit/test_idempotency.py` — `_hash_idempotency_key` (ADOPT-1: tenant-prefix)
- `tests/unit/test_middleware.py` — `_parse_tenant` helper
- `tests/unit/test_tenant.py` — `current_tenant_id` / `require_tenant_id`
- `tests/unit/test_permission_service_db.py` — DB-backed `load_principal` (split from BUG-020)
- `tests/integration/test_idempotency.py` — replay, mismatch, missing-key
- `tests/integration/test_rbac.py` — unauthenticated, demo admin
- `tests/integration/test_rls_isolation.py` — already present, hardened
- `tests/integration/test_health.py` — already present, hardened

### ADRs (`docs/adrs/`)
- `0001-wave-based-delivery.md`
- `0002-pydantic-v2-sqlalchemy-2-async-alembic.md`
- `0003-temporal-for-sagas.md`
- `0004-outbox-apicurio.md`
- `0005-permission-service-everywhere.md`
- `0006-multiline-by-default.md`
- `0007-party-centric-crm.md`
- `0008-soft-delete.md`
- `0009-unleash-feature-flags.md`
- `0010-otel-prometheus.md`
- `0011-outbox-schema-aligns-with-v1.md` (W0 hotfix)
- `0012-idempotency-hash-includes-tenant.md` (W0 hotfix)

### Other
- `docs/GETTING_STARTED.md` — 5-min quick start
- `docs/DEVELOPMENT.md` — branch + module + migration + RBAC + merge-back + coverage
- `docs/TROUBLESHOOTING.md` — 7 common failures
- `docs/trees/erp.md` and `docs/trees/erp-crm.md` — module maps
- `docs/API_CHANGELOG.md` — W0 + W0.1 entries
- `docs/RESEARCH_V1_PATTERNS.md` — 12-area v1 research
- `ADOPT_NOW.md` — 5 P0 items, all adopted in this hotfix
- `BUGS.md` + `MISSING_FILES.md` — audit deliverables
- `services/erp/src/shared/events/__init__.py` — locked event envelope (ADOPT-2)
- `docs/openapi/erp.json` — generated by `dump_openapi.py`, committed

## Audit items closed

| ID | Severity | Description | Closed by |
|----|----------|-------------|-----------|
| BUG-001 | P0 | idempotency_keys table didn't exist | 0102 migration |
| BUG-002 | P0 | tenant_id was String | 0102 + idempotency.py (UUID type) |
| BUG-003 | P0 | set_tenant_context(None) broke ::uuid cast | db.py sentinel + 0101 RLS COALESCE |
| BUG-005 | P0 | permissions composite PK mismatch | 0100 dropped `id` PK |
| BUG-006 | P0 | service-account privilege escalation | service.py `service_account_scopes` |
| BUG-007 | P0 | OTel tracer proxy on first request | middleware.py `init_tracing()` in `__init__` |
| BUG-008 | P0 | tenant_id type churn across 4 call sites | tenant.py + middleware.py UUID contract |
| BUG-009 | P1 | require_permission didn't delegate | deps.py delegates to PermissionService |
| BUG-010 | P1 | dead `PermissionDenied` import | removed |
| BUG-011 | P1 | dead `_is_idempotent_path` | removed |
| BUG-012, BUG-025 | P1 | gitignore contradicted OpenAPI commitment | removed .gitignore |
| BUG-013 | P1 | unused `app.state.metadata` | removed |
| BUG-014 | P1 | init_metrics not strict | raises on registry mismatch |
| BUG-015 | P1 | testcontainers import on no-Docker | conftest try/except (acceptable) |
| BUG-016 | P1 | OTel contextvars not reset | autouse conftest fixture |
| BUG-018 | P1 | load_principal N+1 | single JOIN |
| BUG-019 | P1 | identity/api used `request.state.tenant_id` | Depends(require_tenant_id) |
| BUG-020 | P1 | test_permission_service didn't cover load_principal | split into test_permission_service_db.py |
| BUG-021 | P1 | list_permissions was world-readable | RBAC-gated |
| BUG-022 | P1 | middleware used `trace.get_tracer()` | uses `observability.tracing.tracer()` |
| BUG-023 | P2 | README status stale | wave doc updated |
| BUG-024 | P2 | "placeholder" in __init__ docstrings | removed |
| BUG-027 | P2 | unused pgcrypto extension | removed |
| OPS-1..7, RBAC-1..5, S-1..16 | various | observability + RBAC + cross-cutting | ship in this wave |

**Not closed in W0 (deferred to W1+):**
- BUG-004 (P0, BEFORE-W1) — `/ready` outbox check is informational until W1 ships the poller
- BUG-026 (P2) — third-assertion hardening of RLS test
- BUG-028 (P2) — `ApiError.__init__` double-pass (kept, intentional)
- BUG-029 (P2) — empty wave-1+ scaffolding dirs (wave-owns-the-init convention)
- BUG-030 (P2) — `check_coverage.py` matcher fragility (subclass pattern in W6)

## What this wave does NOT do

- **No business logic.** No sales order, no party, no product, no invoice. W1.
- **No JWT.** W0 trusts `x-user-id`. W1 swaps to PyJWT + Keycloak JWKS (the realm-export is ready).
- **No outbox poller / Kafka producer.** Schema is in 0100; poller + producer land in W1.
- **No per-tenant role seeding in code.** The catalog is global; per-tenant role + permission grants are created in W6's tenant-onboarding flow. The seed migration 0103 is the canned demo.

## How to run locally

```bash
cd services/erp
pip install -e ".[dev]"

# 1. Start Postgres (use the docker-compose or the system PG)
docker compose -f ../../infra/docker-compose.yml --profile core up -d

# 2. Set the URL and apply migrations
export ERP_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_db
make migrate

# 3. Seed the demo data
make seed

# 4. Boot the app
make run
# → http://localhost:8001

# 5. Smoke test (boots in-process, hits every endpoint)
make smoke

# 6. Tests
make test-unit                            # no infra
ERP_TEST_DATABASE_URL=... make test-integration
```

## How to merge into the parent

Push this branch to the parent repo's remote under
`feat/erp-v2-w0-foundations`. Open a PR against
`cautious-goggles/feat/erp-crm` titled:

```
chore(erp-v2): foundations (schemas, RBAC, OTel, idempotency, OpenAPI)
```

Risk: **Low** — additive only, no schema delete, no event rename.
The 0102 migration is additive (new table); 0100 was already
uncommitted-elsewhere and the W0 fix is on the W0 branch.

Rollback: revert the PR; v1 unaffected.

W1 work starts on a fresh `feat/erp-v2-w1-o2c-complete` branch.
