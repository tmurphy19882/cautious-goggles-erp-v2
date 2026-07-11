# Getting Started — ERP v2 in 5 minutes

> **Branch of record:** `feat/erp-v2-w0-foundations`
> **Wave:** W0 — Foundations (no business behaviour, plumbing only)
> **Time to running service:** ~5 min on a clean box with Docker

This is the shortest path from `git clone` to `curl /health`. If
anything in here breaks, jump to [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md).
For the day-to-day development loop, see
[`DEVELOPMENT.md`](./DEVELOPMENT.md).

## 1. Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | **3.12+** | `pyproject.toml` requires `>=3.12`; `match`/PEP 695 syntax in use |
| Docker | 24+ | Just for Postgres in W0; W1+ adds Temporal, Kafka, Apicurio, Unleash |
| Git | 2.30+ | Trunk-based, short-lived branches |
| `psql` (optional) | 14+ | For poking at the DB during dev; not required to run the service |
| `make` (optional) | — | No Makefile in W0; we use `pyproject` scripts |

> **W0 only needs Postgres.** Temporal, Kafka, Apicurio, and Unleash
> arrive in W1+. The W0 boot path does not require them; if their
> env vars are unset, the relevant subsystem is a no-op.

## 2. Clone + install

```bash
git clone https://github.com/tmurphy19882/cautious-goggles-erp-v2.git
cd cautious-goggles-erp-v2
cd services/erp
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The `[dev]` extra pulls in `pytest`, `pytest-asyncio`, `pytest-cov`,
`ruff`, `mypy`, `testcontainers[postgres]`, `faker`, and `freezegun`.
Without it, the test commands at the bottom of this doc won't work.

## 3. Configure `.env`

Copy the sample and edit:

```bash
cp .env.example .env
```

W0 reads three env vars (the rest are W1+):

| Var | Default | Purpose |
|-----|---------|---------|
| `ERP_DATABASE_URL` | `postgresql+asyncpg://erp:erp@localhost:5432/erp_db` | Async SQLAlchemy URL |
| `LOG_LEVEL` | `INFO` | `structlog` level |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(unset)* | Optional; OTel traces are no-op if unset |

> **W0 stubs auth.** There is no real auth. The principal is read
> from the `x-user-id` header. Don't expose port `8001` to the
> internet in W0.

## 4. Run Postgres

Docker is the fastest path. From the repo root:

```bash
docker run --name erp-pg -d \
  -e POSTGRES_USER=erp \
  -e POSTGRES_PASSWORD=erp \
  -e POSTGRES_DB=erp_db \
  -p 5432:5432 \
  postgres:16
```

Verify it's up:

```bash
docker exec -it erp-pg pg_isready -U erp -d erp_db
# /var/run/postgresql:5432 - accepting connections
```

> **Don't have Docker?** Any Postgres 14+ works. Create a database
> and a role with full privileges, then point `ERP_DATABASE_URL`
> at it. Skip the `pgcrypto` / `uuid-ossp` setup — Alembic creates
> the extensions.

## 5. Run migrations

```bash
cd services/erp
export ERP_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_db
alembic upgrade head
```

Expected output:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0100_identity, identity
INFO  [alembic.runtime.migration] Running upgrade 0100_identity -> 0101_identity_rls, identity rls
```

This creates the `users`, `roles`, `permissions`,
`role_permissions`, `user_roles` tables, seeds the global
permission catalog (~50+ entries), and enables FORCE RLS on
every tenant-scoped table.

If you see `permission denied for table users` or `relation
"permissions" does not exist`, jump to
[`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md#1-permission-denied-for-table-users--running-migrations-without-the-right-role).

## 6. Start the app

```bash
uvicorn src.app:app --reload --port 8001
```

You should see:

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8001
```

## 7. Hit the endpoints

Open a second shell.

**Liveness — no DB, no auth:**

```bash
curl -s http://localhost:8001/health | jq
# { "status": "ok", "service": "erp-v2" }
```

**Readiness — pings the DB:**

```bash
curl -s http://localhost:8001/ready | jq
# {
#   "status": "ok",
#   "checks": {
#     "database": { "status": "ok" },
#     "outbox": { "status": "ok", "note": "no outbox poller yet (W0)" },
#     "kafka_producer": { "status": "ok", "note": "no producer yet (W0)" }
#   }
# }
```

**Global permission catalog — no auth in W0 (catalog is global,
not tenant-scoped):**

```bash
curl -s http://localhost:8001/api/v1/erp/identity/permissions | jq 'length'
# 53
curl -s http://localhost:8001/api/v1/erp/identity/permissions | jq '.[0]'
# {
#   "key": "ai.agent.run",
#   "resource": "agent",
#   "action": "run",
#   "description": "Trigger an agent run"
# }
```

**Per-tenant roles — needs `x-tenant-id` + `x-user-id` (W0 stub):**

```bash
curl -s \
  -H "x-tenant-id: 00000000-0000-0000-0000-000000000001" \
  -H "x-user-id: 00000000-0000-0000-0000-000000000002" \
  http://localhost:8001/api/v1/erp/identity/roles | jq
# []  (no roles seeded yet — per-tenant seeding lands in W6)
```

**Metrics — Prometheus scrape format:**

```bash
curl -s http://localhost:8001/metrics | head -20
# HELP http_requests_total HTTP requests handled, labelled by method/route/status/tenant
# TYPE http_requests_total counter
# http_requests_total{method="GET",route="/health",status="200",tenant="anonymous"} 1.0
# ...
```

**OpenAPI schema — auto-generated by FastAPI:**

```bash
curl -s http://localhost:8001/openapi.json | jq '.info'
# {
#   "title": "ERP Service v2",
#   "version": "0.1.0",
#   "description": "Clean rebuild of cautious-goggles/services/erp."
# }
```

## 8. Run tests

**Unit only — no Postgres required:**

```bash
cd services/erp
pytest -m unit
```

Expected:

```
tests/unit/test_errors.py            ...
tests/unit/test_idempotency.py       ...
tests/unit/test_permission_service.py ...
tests/unit/test_schemas.py           ...
4 passed in 0.41s
```

**Integration — needs Postgres:**

```bash
cd services/erp
export ERP_TEST_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_test
# Create the test DB once:
docker exec -it erp-pg createdb -U erp erp_test
pytest -m integration
```

The `conftest.py` creates a per-test schema (so tests don't
trample on each other) and tears it down on exit.

> **"tests skip with 'Postgres not available'"?** That means
> `ERP_TEST_DATABASE_URL` isn't set. See
> [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md#6-tests-skip-with-postgres-not-available--set-erp_test_database_url).

**All tests with coverage:**

```bash
pytest --cov=src --cov-report=term-missing
```

## 9. Tear down

```bash
# Stop the app: Ctrl-C
# Stop Postgres:
docker stop erp-pg
# Wipe the DB (start fresh later):
docker rm erp-pg
# Remove the venv:
rm -rf services/erp/.venv
```

## Next steps

- Read [`DEVELOPMENT.md`](./DEVELOPMENT.md) for the day-to-day dev
  loop (branches, modules, migrations, the coverage guard).
- Read [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md) before you open
  an issue — most "is this a bug" questions are answered there.
- Skim the ADRs in [`docs/adrs/`](./adrs/) for the architectural
  decisions that shape every later wave.
- Look at [`docs/trees/erp.md`](./trees/erp.md) for the module map.
