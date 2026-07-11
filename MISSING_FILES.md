# MISSING_FILES — W0 Foundations

> **Scope:** every file the SPEC, README, MERGE_PLAN, or `WAVE_0_FOUNDATIONS.md`
> *refer to or promise* but that does **not exist** (or exists only as an
> untracked scaffold) on `feat/erp-v2-w0-foundations` at commit `b193226`.
>
> **Categories:** `infra/`, `scripts/`, `tests/`, `docs/`, `services/erp/`.
>
> **Status key:**
> - `absent` — file is referenced in docs/code/README/CI but no path or scaffold exists
> - `untracked` — file exists on disk in this checkout but is not committed
> - `partial` — the directory exists with a placeholder README only

## Summary

| Category         | Count |
|------------------|------:|
| `infra/`         |    11 |
| `scripts/`       |     4 |
| `tests/`         |     4 |
| `docs/`          |    14 |
| `services/erp/`  |     6 |
| **Total**        | **39** |

> **Note on counting:** some entries in the same directory are counted
> separately because each is an independently referenceable deliverable (e.g.
> `docs/adrs/0001-*.md` through `docs/adrs/0010-*.md` are ten entries, not
> one).

---

## `infra/` — local stack

The directory `infra/` exists with one README and seven empty subdirectories
(`apicurio/`, `kafka/`, `keycloak/`, `kong/`, `otel/`, `postgres/`, `unleash/`).
Per `infra/README.md:5-15`, every subdirectory was supposed to ship a
`docker-compose.yml` overlay (or equivalent config) in W0. None do.

### MISSING-001 · `infra/docker-compose.yml` · `absent`
- **What:** top-level local stack compose file referenced in the root `README.md:79` (`docker compose -f infra/docker-compose.yml up -d`).
- **Why it matters:** the documented "Local dev" workflow does not work — a fresh dev has no entry point. Without this, the W0 promise "stand up cross-cutting infra" cannot be verified by a reviewer.

### MISSING-002 · `infra/postgres/docker-compose.yml` and `infra/postgres/init.sql` · `absent`
- **What:** Postgres 16 + the `erp_db` per-service init SQL (extensions `uuid-ossp`, `pgcrypto`, role `erp`, schemas). Per `infra/README.md:8` ("Postgres 16 + per-service DB init").
- **Why it matters:** every test, migration, and developer laptop depends on this. The migration `0100_identity.py` needs `uuid-ossp` to exist before `alembic upgrade head` runs.

### MISSING-003 · `infra/kafka/docker-compose.yml` and `infra/kafka/topics.yaml` · `absent`
- **What:** Redpanda (Kafka API) container + a topic manifest naming every topic W1+ will publish to (e.g. `scm.erp.order-confirmed.v1`). Per `infra/README.md:9` and `MERGE_PLAN.md:113-115` ("Both v1 and v2 define `SalesOrder`… outbox … Identical payload schema").
- **Why it matters:** the wave summary (`WAVE_0_FOUNDATIONS.md:114`) says "the poller and producer land in W1" but the topic manifest should land in W0 so W1's producer has a single source of truth and a CI gate can refuse drift.

### MISSING-004 · `infra/keycloak/realm-export.json` · `absent`
- **What:** Keycloak realm JSON with the `erp-v2` client, the `erp-v2-user` and `erp-v2-service-account` roles, the `x-user-id` / `x-tenant-id` claim mappers. Per `infra/README.md:11` and `services/erp/.env.example:20-23` (W1 JWT issuer).
- **Why it matters:** W1 swaps the `x-user-id` header trust to a real JWT validator (`identity/deps.py:33-57`). The W0 deliverable is not "wait for W1 to introduce Keycloak" — it is "ship the realm export so W1's PR is a one-line change".

### MISSING-005 · `infra/kong/kong.yml` (or `infra/kong/erp-route.yml`) · `absent`
- **What:** Kong declarative config that mounts `/api/v1/erp` → `erp-v2:8001`. Per `infra/README.md:12` and `MERGE_PLAN.md:101` ("Kong: `infra/kong/erp-route.yml` removes the legacy shadow route").
- **Why it matters:** without this, the path-prefix `/api/v1/erp` (set in `api/main.py:104`) is meaningless to operators. W0 ships the factory; the W0 PR should also ship the Kong route that fronts it.

### MISSING-006 · `infra/apicurio/docker-compose.yml` and `infra/apicurio/global-rules.json` · `absent`
- **What:** Apicurio Schema Registry container + the global compatibility rules (`FULL` compatibility for every topic). Per `infra/README.md:10` and SPEC §2 ("Avro — every event payload validated against … Apicurio").
- **Why it matters:** SPEC §2 says Apicurio is a "first-class" cross-cutting concern that gates CI. W0 promised to "stand up cross-cutting infra" — Apicurio is one of them.

### MISSING-007 · `infra/otel/collector-config.yaml` · `absent`
- **What:** OpenTelemetry Collector config (receivers: OTLP gRPC + HTTP; processors: batch, resource, tail_sampling; exporters: Jaeger, Prometheus, logging). Per `infra/README.md:13` and the env var documented in `services/erp/.env.example:16`.
- **Why it matters:** `observability/tracing.py:42-47` checks `OTEL_EXPORTER_OTLP_ENDPOINT`; if unset, spans are in-memory only ("no OTLP endpoint set, spans are in-memory only"). The W0 deliverable is the collector config that the dev points the env var at.

### MISSING-008 · `infra/unleash/docker-compose.yml` and `infra/unleash/flags.json` · `absent`
- **What:** Unleash container with the `erp.v2.<module>.enabled` flag seeds. Per `infra/README.md:14` and SPEC §2 cross-cutting rules referencing per-tenant feature flags.
- **Why it matters:** `MERGE_PLAN.md:83-92` repeatedly invokes `erp.v2.o2c.enabled` style flags as the cutover mechanism. The flag system needs to exist on day 0 of W0 even if every flag is `true` for the bootstrap tenant.

### MISSING-009 · `infra/temporal/docker-compose.yml` · `absent`
- **What:** Temporal server + the W1+ namespace bootstrap. Per `infra/README.md:15` ("Temporal — W1/W2").
- **Why it matters:** listed in the infra table; not W0-blocking, but the placeholder directory should either be created in W0 with a "lands in W1" note, or be removed from the README. Right now the README claims it exists and the directory is empty.

### MISSING-010 · `infra/minio/docker-compose.yml` · `absent`
- **What:** MinIO container for W6's import-export staging. Per `infra/README.md:16`.
- **Why it matters:** same as MISSING-009 — either the README or the directory should be reconciled. Low priority.

### MISSING-011 · `infra/postgres/seed-permset.sql` (or equivalent) · `absent`
- **What:** initial `erp` role grant: `CREATE ROLE erp LOGIN PASSWORD 'erp'; GRANT ALL ON SCHEMA public TO erp;` plus the `app.tenant_id` GUC grant.
- **Why it matters:** even on a single-developer laptop, `services/erp/migrations/env.py:34-36` reads `ERP_DATABASE_URL` to set the migration target. Without the role and grants, `alembic upgrade head` fails with `permission denied for schema public`. The W0 deliverable should include the `init.sql` that makes `alembic upgrade head` succeed on a fresh Docker container.

---

## `scripts/` — root-level dev helpers

The root `scripts/` directory contains one `README.md` only. The README
mentions four planned scripts, none of which exist. `services/erp/scripts/`
has two (per W0 wave) — those are not duplicates, just the service-level
ones.

### MISSING-012 · `scripts/bootstrap_local.sh` · `absent`
- **What:** one-shot dev script — `docker compose -f infra/docker-compose.yml up -d; sleep 5; cd services/erp && alembic upgrade head; python scripts/seed_demo.py`. Per `scripts/README.md:7`.
- **Why it matters:** the wave's value proposition is "local dev works" — this script is the one-command test. Without it, every developer repeats the same five steps manually.

### MISSING-013 · `scripts/seed_demo_tenant.py` · `absent`
- **What:** creates the `acme` tenant, the `admin@acme.com / admin123` user, the `admin` role, the grants to every permission, and a `demo-tenant-id` UUID the README can pin to. Per `scripts/README.md:8`.
- **Why it matters:** BUG-006 (service accounts = all perms) means a real user is the only way to exercise the permission catalog. The current `seed_demo_tenant.py` *not existing* means every integration test would need to seed its own user, which is exactly what the conftest should be doing — but it doesn't. Without the seed, the `client` fixture in `conftest.py:118-131` cannot produce a useful integration test for the `identity/roles` endpoint.

### MISSING-014 · `scripts/check_event_coverage.py` · `absent`
- **What:** static-analysis guard: every event topic in the SPEC §2 events list has an Avro schema in `packages/shared-events/schemas/` AND a producer entry in the outbox topic registry. Per `scripts/README.md:10`.
- **Why it matters:** SPEC §2 calls this a "CI gate" — it should be a script. The WAVE_0 doc claims it ("CI gate (`validate_manifest.py`) refuses merges with drift"), but the script doesn't ship in W0.

### MISSING-015 · `scripts/check_rbac_coverage.py` · `absent`
- **What:** static-analysis guard: every mutating route in *every* service (`o2c`, `p2p`, `crm`, etc.) depends on `require_permission`. The service-local `services/erp/scripts/check_coverage.py` is a W0 prototype; the root script should be cross-service once W1+ lands. Per `scripts/README.md:11`.
- **Why it matters:** the wave summary (line 82-83) promises `erp-v2-check-coverage` is "wired as a static analysis guard". The shipped version is fragile (BUG-030) and is scoped to one service. A root-level script is the eventual home.

---

## `tests/` — coverage gaps

The service's `tests/` directory is complete for W0 unit + integration
coverage of identity / health / RLS. Three named gaps remain.

### MISSING-016 · `tests/contract/` · `absent`
- **What:** the directory `tests/contract/` (per `services/erp/README.md:30` and `AUDIT.md § S-17`). The wave summary does not require contract tests in W0 — they are a W10 deliverable — but the directory itself, with a README and a placeholder test, is conventionally created at W0 so W10's PR is purely additive.
- **Why it matters:** without the placeholder, W10's PR will create a directory and a fixture in one shot, which is harder to review than two diffs.

### MISSING-017 · `tests/unit/test_tenant.py` · `absent`
- **What:** unit tests for `shared/tenant.py` — `current_tenant_id` returns the request state value; `require_tenant_id` raises `TenantRequiredError` when missing; raises on malformed UUID.
- **Why it matters:** BUG-008 is the kind of bug a unit test would have caught. The file does not exist; the only `tenant.py` coverage is the route-level assertion in `test_health.py:60-61` (the integration test for the 401/400 on missing header).

### MISSING-018 · `tests/unit/test_middleware.py` · `absent`
- **What:** unit tests for `observability/middleware.py` — request_id roundtrip, `trace_id_var` and `span_id_var` reset behaviour, the `route` label fallback, the excluded-paths early-return, the latency-histogram observation.
- **Why it matters:** BUG-007, BUG-016, and BUG-022 all live in this file. None are exercised by a unit test; the only "test" is a by-effect assertion in `test_health.py:33-38` that the `/metrics` endpoint responds with `text/plain`. That assertion does not catch any of the OTel bugs.

### MISSING-019 · `tests/integration/test_idempotency_replay.py` · `absent`
- **What:** integration test: POST `/api/v1/erp/identity/roles` with `Idempotency-Key: K1`; expect 200 (or 201). Replay with same key, same body; expect identical response. Replay with same key, different body; expect 409 `idempotency_key_mismatch`. Missing key on a mutating request; expect 400 `idempotency_key_required`.
- **Why it matters:** MERGE_PLAN.md:60 says every wave ships "idempotency-replay" test coverage. W0 is the wave that ships the middleware — it should also ship the replay test. The current `test_idempotency.py:1-32` only tests the `_hash_request_body` helper, not the middleware.

---

## `docs/` — documentation gaps

`docs/` is a near-complete skeleton but several referenced files are
missing.

### MISSING-020 · `docs/parent-pr-template.md` · `absent`
- **What:** the per-wave PR template, per `MERGE_PLAN.md:40` ("lives at `docs/parent-pr-template.md` in W0").
- **Why it matters:** `MERGE_PLAN.md:40` explicitly says this file ships in W0. Without it, the W1 PR author has to copy the skeleton from `MERGE_PLAN.md:42-77` by hand.

### MISSING-021 · `docs/trees/erp.md` · `untracked`
- **What:** the file *does exist* in the working directory (10 KB) but is **not committed**. It is the canonical file map for the v2 ERP service. Per the task brief and `docs/trees/` being referenced by the SPEC.
- **Why it matters:** the working tree has the file; `git status` shows it under "Untracked files". A reviewer who clones fresh sees an empty `docs/trees/` directory. This is the highest-priority "missing" item because it is *almost* shipped.

### MISSING-022 · `docs/trees/erp-crm.md` · `absent`
- **What:** the combined ERP+CRM tree, per the task brief.
- **Why it matters:** `AUDIT.md § S-11` says the CRM module lives under ERP per ADR-0016. A combined tree would be the canonical reference once W5 lands; W0 should at least seed the file with a TODO.

### MISSING-023 · `docs/GETTING_STARTED.md` · `absent`
- **What:** the developer onboarding doc — clone, `docker compose -f infra/docker-compose.yml up -d`, `cd services/erp && pip install -e ".[dev]"`, `alembic upgrade head`, `uvicorn src.app:app --reload --port 8001`, `pytest`. The wave summary (`WAVE_0_FOUNDATIONS.md:120-144`) has a "How to run locally" section, but the README chains to a `docs/GETTING_STARTED.md` that does not exist.
- **Why it matters:** the W0 doc's "How to run locally" is good; the root `README.md:74-86` is a placeholder that points at the missing file. Onboarding a new dev requires the placeholder to be filled in.

### MISSING-024 · `docs/DEVELOPMENT.md` · `absent`
- **What:** the dev workflow doc — coding conventions, ruff/mypy/pytest invocation, branch workflow, PR template. Per the task brief.
- **Why it matters:** every other sibling repo has this file; this repo's README is the placeholder.

### MISSING-025 · `docs/TROUBLESHOOTING.md` · `absent`
- **What:** the FAQ — "Why am I getting `UndefinedTableError: relation "idempotency_keys"`?" (BUG-001), "Why is the OTel collector missing in my Docker stack?" (MISSING-007), "Why are my integration tests skipped?" (testcontainers import error, BUG-015).
- **Why it matters:** the wave summary admits the first three of these are real. The doc should pre-empt them.

### MISSING-026 · `docs/adrs/0001-wave-based-delivery.md` · `absent`
- **What:** ADR-0001 per `docs/adrs/README.md:7` ("Wave-based delivery, no big-bang cutover — **Accepted**"). The README lists ten ADRs as accepted; zero file bodies exist.
- **Why it matters:** "Accepted" implies a written decision. The README explicitly says ("ADRs are written as each wave's work forces a decision; not all up front.") — so ADRs are *allowed* to be unwritten, but listing them as **Accepted** in the table is misleading.

### MISSING-027 · `docs/adrs/0002-pydantic-sqlalchemy-alembic.md` · `absent`
- **What:** ADR-0002 body. Per `docs/adrs/README.md:8`.
- **Why it matters:** same as MISSING-026.

### MISSING-028 · `docs/adrs/0003-temporal-for-sagas.md` · `absent`
- **What:** ADR-0003 body. Per `docs/adrs/README.md:9`.
- **Why it matters:** same.

### MISSING-029 · `docs/adrs/0004-outbox-apicurio.md` · `absent`
- **What:** ADR-0004 body. Per `docs/adrs/README.md:10`.
- **Why it matters:** same.

### MISSING-030 · `docs/adrs/0005-permission-service-everywhere.md` · `absent`
- **What:** ADR-0005 body. Per `docs/adrs/README.md:11`.
- **Why it matters:** same.

### MISSING-031 · `docs/adrs/0006-multiline-by-default.md` · `absent`
- **What:** ADR-0006 body. Per `docs/adrs/README.md:12`.
- **Why it matters:** same.

### MISSING-032 · `docs/adrs/0007-party-centric-crm.md` · `absent`
- **What:** ADR-0007 body. Per `docs/adrs/README.md:13`.
- **Why it matters:** same.

### MISSING-033 · `docs/adrs/0008-soft-delete.md` · `absent`
- **What:** ADR-0008 body. Per `docs/adrs/README.md:14`.
- **Why it matters:** same.

### MISSING-034 · `docs/adrs/0009-unleash-feature-flags.md` · `absent`
- **What:** ADR-0009 body. Per `docs/adrs/README.md:15`.
- **Why it matters:** same.

### MISSING-035 · `docs/adrs/0010-otel-prometheus.md` · `absent`
- **What:** ADR-0010 body. Per `docs/adrs/README.md:16`.
- **Why it matters:** same.

> **MISSING-026..035 group verdict:** the W0 README (`docs/adrs/README.md`)
> acknowledges that ADRs are written as waves force them. The list is
> forward-looking. A reviewer might choose to either delete the rows
> (make the table honest) or seed each row with a one-paragraph stub.
> As shipped, the **Accepted** status of unwritten ADRs is a documentation
> bug separate from the missing files themselves.

---

## `services/erp/` — service-level gaps

### MISSING-036 · `services/erp/Dockerfile` · `absent`
- **What:** multi-stage Dockerfile — builder stage with `python:3.12-slim`, runtime stage with `python:3.12-slim`, `pip install --no-deps`, non-root user, `CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8001"]`. Per the root `README.md:79` ("Local dev" workflow assumes Docker).
- **Why it matters:** without a Dockerfile, the `infra/docker-compose.yml` (MISSING-001) has no image to build. W0 is the wave that ships the factory — the container image is the deployable artifact.

### MISSING-037 · `services/erp/.dockerignore` · `absent`
- **What:** `.dockerignore` excluding `tests/`, `__pycache__/`, `.git/`, `.venv/`, `htmlcov/`, `.pytest_cache/`, `*.md` (except LICENSE), `docs/`, etc.
- **Why it matters:** without this, every `docker build` copies the entire repo into the build context, blowing up the build time and the image size. The shipped `.gitignore` is not the same file.

### MISSING-038 · `services/erp/Makefile` · `absent`
- **What:** `make install`, `make migrate`, `make test`, `make test-unit`, `make test-integration`, `make run`, `make fmt`, `make lint`, `make typecheck`, `make openapi`, `make coverage`. The wave summary uses bare shell commands (`pip install -e ".[dev]"`, `alembic upgrade head`, `uvicorn src.app:app --reload --port 8001`, `pytest -m unit`).
- **Why it matters:** a Makefile is the single-command source of truth for the W0 dev workflow. Without it, every dev types the same seven commands. The wave summary itself is begging to be a Makefile.

### MISSING-039 · `services/erp/scripts/seed_demo.py` · `absent`
- **What:** the *service-level* version of `scripts/seed_demo_tenant.py` (MISSING-013). Per the scripts/README and the wave summary promise ("`scripts/seed_demo.py` — admin user + role grants" from the task brief).
- **Why it matters:** the service-level script can use the `shared.db` engine directly and bypass the `app` factory. The root-level script should be a thin wrapper around the service-level one. Right now neither exists.

### MISSING-040 · `services/erp/scripts/smoke_test.py` · `absent`
- **What:** boot-the-app-and-curl test — `uvicorn src.app:app` in a subprocess, wait for `/health` to return 200, hit `/api/v1/erp/identity/permissions` and assert non-empty, hit `/metrics` and assert `http_requests_total` is present, kill the subprocess, exit 0.
- **Why it matters:** the wave summary's "How to run locally" section is a *manual* smoke test. A scripted version would let CI catch the obvious "the app doesn't even boot" regression on every PR.

### MISSING-041 · `services/erp/.env` · `absent` (and not expected to be committed)
- **What:** the real `.env` file (gitignored — see `.gitignore:11-13`).
- **Why it matters:** `.env.example` exists; a developer copies it to `.env` and edits. This is a "missing in the *instructions*", not a missing file in the repo. Calling it out so the onboarding doc (MISSING-023) covers the step.

---

## Cross-references: what's NOT missing (and the task brief said might be)

| Item in the task brief | Verdict | Why |
|----------------------|---------|-----|
| `services/erp/.env.example` | **Present** | `services/erp/.env.example:1-23`. |
| `services/erp/pyproject.toml` | **Present** | `services/erp/pyproject.toml:1-127`. |
| `services/erp/migrations/env.py` | **Present** | `services/erp/migrations/env.py:1-74`. |
| `services/erp/migrations/versions/0100_identity.py` | **Present** | full file. |
| `services/erp/migrations/versions/0101_identity_rls.py` | **Present** | full file. |
| `services/erp/src/app.py` | **Present** | re-exports `create_app_v2`. |
| `services/erp/src/api/health.py` | **Present** | `services/erp/src/api/health.py:1-68`. |
| `services/erp/src/api/main.py` | **Present** | `services/erp/src/api/main.py:1-109`. |
| `services/erp/src/identity/*` | **Present** | all 5 sub-files. |
| `services/erp/src/observability/*` | **Present** | all 4 sub-files. |
| `services/erp/src/shared/*` | **Present** | all 6 sub-files. |
| `services/erp/tests/conftest.py` | **Present** | full file. |
| `services/erp/tests/unit/*` | **Present** | all 4 test files. |
| `services/erp/tests/integration/*` | **Present** | all 2 test files. |
| `services/erp/scripts/dump_openapi.py` | **Present** | but see BUG-012 for the gitignore problem. |
| `services/erp/scripts/check_coverage.py` | **Present** | but see BUG-030 for the matcher fragility. |
| `docs/openapi/README.md` | **Present** | but see BUG-025 for the contradiction. |
| `.github/workflows/ci.yml` | **Present** | but it does not run `erp-v2-dump-openapi` (see BUG-012). |

---

## Quick-build priority (for the W0 hotfix PR)

If the goal is to close the smallest set of *files* that makes W0 reviewable
as a real deliverable:

1. **MISSING-021** (commit `docs/trees/erp.md`) — it already exists; just `git add`.
2. **MISSING-020** (`docs/parent-pr-template.md`) — copy from `MERGE_PLAN.md:42-77`.
3. **MISSING-036 + MISSING-037** (`Dockerfile` + `.dockerignore`) — needed for any deploy story.
4. **MISSING-038** (`Makefile`) — codifies the W0 dev workflow.
5. **MISSING-002** (`infra/postgres/init.sql` + `docker-compose.yml`) — unblocks `alembic upgrade head` for everyone.
6. **MISSING-013** (`scripts/seed_demo_tenant.py`) — unblocks the integration test for `identity/roles` and demonstrates the RBAC stack end-to-end.
7. **MISSING-001** (`infra/docker-compose.yml`) — wires the above together.

Everything else can wait for W1 or for a dedicated docs pass.
