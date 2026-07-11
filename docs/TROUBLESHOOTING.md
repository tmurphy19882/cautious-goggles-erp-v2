# Troubleshooting — Common failures and fixes

> **Read this first.** Most "is this a bug" questions are answered
> here. If a fix below doesn't work, capture the *exact* error
> string, the command you ran, the wave (`W0..W10`), and the OS.

Each entry is:

- **Symptom** — what you see.
- **Cause** — why.
- **Fix** — what to do, in order of effort.

---

## 1. `permission denied for table users` — running migrations without the right role

**Symptom**

```
alembic.util.exc.CommandError: Can't locate revision identified by '0100_identity'
... or, later, during `alembic upgrade head`:
psycopg2.errors.InsufficientPrivilege: permission denied for table users
```

(or the asyncpg equivalent:
`asyncpg.exceptions.InsufficientPrivilegeError: permission denied for table users`)

**Cause**

The Postgres role the migration runs as does not own the
`users` / `roles` / `permissions` / `role_permissions` /
`user_roles` tables. This happens when:

- The `ERP_DATABASE_URL` points at a role that was created
  with limited privileges (e.g. `GRANT SELECT` only).
- Migrations were run by a different role (often `postgres`)
  than the app's role; the app role can read but not
  `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`.

**Fix**

1. **For local dev:** run migrations as the role that *owns*
   the database, then connect the app as a less-privileged
   role. The `docker run` example in
   [`GETTING_STARTED.md`](./GETTING_STARTED.md#4-run-postgres)
   uses the `erp` superuser-style role for both — that's
   fine for local dev, not for staging.

2. **For staging / prod:** the migration role and the app
   role are the same, and they own the schema. Migrations
   run with the *owner* role; the app's runtime role has
   `SELECT / INSERT / UPDATE / DELETE` on the tables but
   never `OWNER`. Confirm with:

   ```sql
   \dt identity.*
   -- Owner column should match the role in ERP_DATABASE_URL
   ```

3. **If the tables were created by a different role** (e.g.
   `postgres` from a `createdb` step):

   ```sql
   REASSIGN OWNED BY postgres TO erp;
   GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO erp;
   GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO erp;
   ```

   Then re-run `alembic upgrade head`. The down-then-up
   round trip is safe; the catalog is `INSERT ... ON CONFLICT
   DO NOTHING` in `0100_identity.py` and re-running is a
   no-op for the catalog rows.

---

## 2. `RLS policy violation` — `app.tenant_id` GUC not set

**Symptom**

```
asyncpg.exceptions.RaiseException: new row violates row-level security policy for table "users"
```

or, for reads:

```
(psycopg2.RaiseException) no rows returned (RLS silently filtered the only matching row)
```

**Cause**

Every tenant-scoped table in v2 has
`ALTER TABLE ... FORCE ROW LEVEL SECURITY` plus a policy
that compares `tenant_id` to `current_setting('app.tenant_id')`.
If the GUC is unset, the policy returns `false` for every
row, so reads return nothing and writes fail.

The GUC is set by `shared.db.set_tenant_context(session,
tenant_id)` which is called from the request session
lifecycle (the `IdempotencyMiddleware` resolves the tenant
from `x-tenant-id` and the session's first query sets the
GUC). If the call is missing — e.g. you opened a session
manually in a script without going through the request path
— the GUC stays unset.

**Fix**

1. **In tests / scripts:** call `set_tenant_context`
   explicitly:

   ```python
   from shared.db import set_tenant_context
   from uuid import UUID

   async with session_factory() as session:
       await set_tenant_context(session, UUID("00000000-0000-0000-0000-000000000001"))
       rows = (await session.execute(select(User))).scalars().all()
   ```

2. **In a request handler:** confirm the route declares
   `Depends(require_tenant_id)` (or one of the
   `require_permission` deps, which transitively depends on
   it). A handler that uses the session factory directly
   without the dep is the cause.

3. **In the admin / out-of-band tooling:** the
   `set_tenant_context` call is the chokepoint. A tool that
   bypasses it (and so bypasses RLS) is a security incident;
   roll the role back and add the call.

4. **Confirm the GUC is set per session:**

   ```sql
   SHOW app.tenant_id;
   -- 00000000-0000-0000-0000-000000000001
   ```

   The GUC is per-connection (set via `SET LOCAL`), so each
   pooled session must call `set_tenant_context` on every
   checkout. The `shared/db.py` session scope does this for
   app code; out-of-band scripts must do it themselves.

---

## 3. `Idempotency-Key required` — forgot the header on a POST

**Symptom**

```http
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "code": "idempotency_key_required",
  "message": "Idempotency-Key header required for mutating requests"
}
```

**Cause**

`IdempotencyMiddleware` (in `src/shared/idempotency.py`)
enforces the `Idempotency-Key` header on every
`POST / PUT / PATCH / DELETE` request. If the header is
missing, the middleware short-circuits with a 400 envelope.

**Fix**

1. **In tests / curl:** add the header. Generate a fresh
   UUID per call (the key is the dedup token; reusing one
   returns the cached response):

   ```bash
   curl -X POST http://localhost:8001/api/v1/erp/... \
     -H "Idempotency-Key: $(uuidgen)" \
     -H "x-tenant-id: 00000000-0000-0000-0000-000000000001" \
     -H "x-user-id: 00000000-0000-0000-0000-000000000002" \
     -H "Content-Type: application/json" \
     -d '{ ... }'
   ```

2. **In the SDK:** every generated client wraps mutating
   calls in an idempotency interceptor that reads from a
   per-call UUID. If the SDK doesn't add the header, the
   generator's template is wrong; check the OpenAPI
   extension `x-idempotent: true` on the route.

3. **If you *really* need an opt-out:** add the path to
   `IdempotencyMiddleware._is_idempotent_path(...)` *and*
   add a comment explaining why. The default is `False` —
   a mutator that is idempotent-by-design (e.g. an upsert
   keyed on a unique constraint) is a candidate, but the
   opt-out is rare.

---

## 4. `idempotency_keys relation does not exist` — migration not run

**Symptom**

```
asyncpg.exceptions.UndefinedTableError: relation "idempotency_keys" does not exist
```

or, in the response:

```json
{ "code": "internal_error", "message": "relation \"idempotency_keys\" does not exist" }
```

**Cause**

The `idempotency_keys` table is referenced by the
`DbIdempotencyStore` (W0) and is created by the
`0100_outbox` migration (W1). In W0 the table is declared
as a SQLAlchemy `Table` reflect-only definition in
`shared/idempotency.py`; if the underlying table is missing
(the migration wasn't run, or you're on a fresh DB), the
first `INSERT` fails.

**Fix**

1. **Run the migration.** In W0 the table is part of
   `alembic upgrade head` (the W0 migration set ships it
   via a follow-up that lands in the same `alembic
   upgrade head` invocation). If you're on a fresh DB:

   ```bash
   cd services/erp
   alembic upgrade head
   ```

2. **If the table is missing on a non-fresh DB:** check
   `alembic_version`:

   ```sql
   SELECT * FROM alembic_version;
   -- If null, the DB has never been stamped. Run:
   -- `alembic stamp head` then `alembic upgrade head`
   ```

3. **If the DB is shared with another service** (rare in
   dev): the `idempotency_keys` table is owned by this
   service. Confirm with `\dt idempotency_keys`.

4. **W0 caveat:** the W0 migrations are
   `0100_identity` + `0101_identity_rls`. The
   `idempotency_keys` table is created by a W1 migration
   (`0102_outbox` or similar). If you're on a W0-only
   build, the W1 migration hasn't landed yet, and the
   store has nowhere to write. Two options:
   - Skip the W0 idempotency integration tests (they
     require the table). The unit tests for the
     `IdempotencyRecord` / hash helpers work without the
     DB.
   - Manually create the table from
     `shared/idempotency.py`'s `IdempotencyKeyTable`
     declaration (it has the column shape). The
     auto-create on first write lands in W1.

---

## 5. `OTLP endpoint unreachable` — observability is optional

**Symptom**

```
opentelemetry.exporter.otlp.proto.grpc.exporter.OTLPExporterSpanExporter: 
  Export failed: <urlopen error [Errno 111] Connection refused>
```

or, every minute:

```
WARNING: Failed to export batch to endpoint: ...
```

**Cause**

W0's `init_tracing()` reads
`OTEL_EXPORTER_OTLP_ENDPOINT` and initialises the OTLP gRPC
exporter. If the env var points at a non-reachable
collector (no OTel collector in `docker compose`, the local
port is wrong, or the network is partitioned), the SDK
retries the export and logs the warning. The service is
otherwise healthy.

**Fix**

1. **It's not a hard dependency in W0.** The trace exporter
   failure does not stop the service. To silence the
   warnings while you dev, unset the env var:

   ```bash
   unset OTEL_EXPORTER_OTLP_ENDPOINT
   # or in .env, comment it out
   ```

   With the env var unset, `init_tracing()` is a no-op;
   spans are emitted to a no-op sink; the metrics endpoint
   still works.

2. **To run a real OTel collector locally:**

   ```bash
   docker run --name otel-collector -d \
     -p 4317:4317 \
     -p 4318:4318 \
     -v "$PWD/infra/otel-collector.yaml:/etc/otelcol/config.yaml" \
     otel/opentelemetry-collector:0.100.0
   export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
   ```

   The collector config is a stub in W0; the W10 plan
   ships a real one.

3. **To verify the SDK is actually exporting:**

   ```bash
   curl -s http://localhost:8001/api/v1/erp/identity/permissions > /dev/null
   # tail the collector logs
   docker logs otel-collector | tail
   ```

---

## 6. Tests skip with `Postgres not available` — set `ERP_TEST_DATABASE_URL`

**Symptom**

```
tests/integration/test_health.py s
tests/integration/test_rls_isolation.py s
2 skipped in 0.12s
```

or:

```
E   pytest.PytestSkip: Postgres not available; set ERP_TEST_DATABASE_URL to enable integration tests
```

**Cause**

The integration tests need a real Postgres. The W0 `conftest.py`
probes `ERP_TEST_DATABASE_URL`; if it's unset, the session-scoped
fixture is skipped and every test in `tests/integration/` is
marked skipped.

**Fix**

1. **Set the env var** to a throwaway DB:

   ```bash
   export ERP_TEST_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_test
   ```

2. **Create the test DB once:**

   ```bash
   docker exec -it erp-pg createdb -U erp erp_test
   ```

3. **Run integration tests:**

   ```bash
   cd services/erp
   pytest -m integration
   ```

4. **Cleanup:** the per-test schema is created and dropped
   inside the test, but the `erp_test` database persists. To
   wipe it: `docker exec -it erp-pg dropdb -U erp erp_test`.

5. **CI:** the GitHub Actions workflow spins up a Postgres
   service container and sets the env var automatically. The
   skip is local-dev only.

---

## 7. `ImportError: cannot import name 'X' from 'shared'` — import rules

**Symptom**

```
ImportError: cannot import name 'ErrorEnvelope' from 'shared' 
(/.../services/erp/src/shared/__init__.py)
```

or, more commonly:

```
ImportError: cannot import name 'require_tenant_id' from 'shared.tenant'
```

**Cause**

`src/shared/__init__.py` is intentionally *sparse*. It
re-exports only the public surface (a small number of
names); for everything else, import from the submodule.

The current re-exports (W0) are:

- `from shared.errors import ApiError, ErrorEnvelope, ...`
- `from shared.schemas import AppModel, TenantBoundModel, ...`
- `from shared.db import Base, create_engine, ...`
- `from shared.idempotency import IdempotencyMiddleware, ...`
- `from shared.tenant import require_tenant_id, current_tenant_id`

If the name you want is **not** in the re-export list, import
from the submodule directly. Example:

```python
# WRONG:
from shared import require_tenant_id

# RIGHT:
from shared.tenant import require_tenant_id
```

**Fix**

1. **Import from the submodule** that owns the name. The
   rule of thumb: the file you need is the one whose
   `__init__` docstring lists the symbol. If you're unsure,
   read the `__init__.py` of `shared/`.

2. **If a name is genuinely part of the public surface**
   but not re-exported, add it to `src/shared/__init__.py`'s
   `__all__` and the re-export `from X import Y` line.
   Edit only if you're sure the name is intended to be
   public; otherwise leave the submodule path.

3. **For `observability`:** the same rule applies — use
   `from observability.metrics import metrics` (or
   `init_metrics()`); not `from observability import metrics`.

4. **For `identity`:** the same rule — `from identity.deps
   import require_permission`; not `from identity import
   require_permission`.

The import rule is a deliberate choice to keep the package
public surface small. A reader who sees `from shared.X
import Y` knows exactly which file to open. A reader who
sees `from shared import Y` has to grep.

---

## Where to go next

- For setup-from-scratch issues: [`GETTING_STARTED.md`](./GETTING_STARTED.md).
- For the day-to-day dev loop: [`DEVELOPMENT.md`](./DEVELOPMENT.md).
- For the wave-by-wave plan: [`MERGE_PLAN.md`](../MERGE_PLAN.md).
- For the architectural decisions: [`docs/adrs/`](./adrs/).
- If a failure here doesn't match one of the seven above,
  file an issue on the wave's branch with the exact error
  string, the command, the wave, and the OS. The fix lands
  here as a new bullet.
