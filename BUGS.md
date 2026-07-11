# BUGS — W0 Foundations

> **Scope:** every bug, inconsistency, or sloppy shortcut found in `services/erp/`
> on `feat/erp-v2-w0-foundations` at commit `b193226`. Read-only audit;
> **no source files were modified.**
>
> **Severity scale:**
> - **P0** — blocks correct behavior; user-impacting
> - **P1** — wrong or fragile; works around in practice
> - **P2** — cosmetic / dead code / documentation drift
>
> **Status key:** `confirmed` = reproduced or directly observable; `suspected` =
> inferred from code shape.

## Summary

| Severity | Count |
|----------|------:|
| P0       |     8 |
| P1       |    12 |
| P2       |    10 |
| **Total**| **30** |

---

## P0 — Blocks correct behavior

### BUG-001 · `idempotency_keys` table is referenced in code but no migration creates it  · **P0** · `confirmed`
- **Where:** `services/erp/src/shared/idempotency.py:95-105` (Table reflection) and `services/erp/src/shared/idempotency.py:160-178` (`pg_insert` / `sqlite_insert`).
- **What's wrong:** the `DbIdempotencyStore` declares a `Table("idempotency_keys", ...)` against `Base.metadata` and executes `INSERT` / `SELECT` / `DELETE` against it on every mutating request, but **no migration in `services/erp/migrations/versions/` ever creates that table**. `0100_identity.py` covers users/roles/permissions/user_roles/role_permissions only; `0101_identity_rls.py` only enables RLS on the identity tables. On a fresh DB the first `POST` will fail with `UndefinedTableError: relation "idempotency_keys" does not exist`. The wave summary explicitly claims "The DB and `idempotency_keys` table are ready" — that claim is false.
- **Fix:** add a new migration `0102_idempotency_keys.py` that does
  ```python
  op.create_table(
      "idempotency_keys",
      sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),  # see BUG-002
      sa.Column("key_hash", sa.String(64), primary_key=True),
      sa.Column("request_hash", sa.String(64), nullable=False),
      sa.Column("response_body", postgresql.JSONB, nullable=False),
      sa.Column("status", sa.Integer, nullable=False),
      sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
      sa.Index("ix_idempotency_keys_expires_at", "expires_at"),
  )
  ```
  And in `0101_identity_rls.py` (or a new follow-up) add `ENABLE/FORCE ROW LEVEL SECURITY` plus a `tenant_id = current_setting('app.tenant_id', true)::uuid` policy so idempotency records are tenant-scoped.

### BUG-002 · `idempotency_keys.tenant_id` column is `String`, should be `UUID` · **P0** · `confirmed`
- **Where:** `services/erp/src/shared/idempotency.py:98` — `Column("tenant_id", String, primary_key=True)`.
- **What's wrong:** every other tenant-scoped table (`users`, `roles`, `role_permissions`, `user_roles`, and all future tables per SPEC §2 "Multi-tenancy") uses `UUID NOT NULL`. Storing `tenant_id` as `String` here means the `idempotency_keys` row cannot be joined to any other tenant-scoped table without an explicit cast, the table will not be picked up by tenant-id indexes, and the value comparison `IdempotencyKeyTable.c.tenant_id == str(tenant_id)` (line 117) silently discards the type guarantee. Once BUG-001's migration is added, this column needs to be `postgresql.UUID(as_uuid=True)` so that RLS on it (see BUG-001 fix) works the same as the rest of the schema.
- **Fix:** in the new migration from BUG-001, declare the column as `postgresql.UUID(as_uuid=True)`, primary-key composite with `key_hash`. Drop the `str(tenant_id)` cast on line 117 — pass the `UUID` directly.

### BUG-003 · `set_tenant_context(None)` writes empty string to GUC, which then breaks RLS `::uuid` cast · **P0** · `confirmed`
- **Where:** `services/erp/src/shared/db.py:87` — `await session.execute(text("SET LOCAL app.tenant_id = ''"))`.
- **What's wrong:** the RLS policy in `0101_identity_rls.py:35` does `tenant_id = current_setting('app.tenant_id', true)::uuid`. `current_setting(..., true)` returns the empty string (not NULL) when the GUC is set to `''`. Postgres then tries `''::uuid` which raises `invalid input syntax for type uuid`. Result: any read or write on a tenant-scoped table from a session that called `set_tenant_context(session, None)` (e.g. a bootstrap / health probe that goes through the session factory) will throw. The docstring on `set_tenant_context` claims this path "return[s] no rows" — in practice it errors.
- **Fix:** either (a) use `SET LOCAL app.tenant_id = NULL` (needs `SET LOCAL ... = NULL` syntax which Postgres allows) and update the RLS policy to handle NULL cleanly, or (b) change the policy to `tenant_id::text = current_setting('app.tenant_id', true)` with the cast guarded, or (c) introduce a sentinel UUID like `'00000000-0000-0000-0000-000000000000'` for "no tenant" and check `current_setting('app.tenant_id', true) <> '' AND tenant_id = current_setting('app.tenant_id', true)::uuid`. Option (a) is cleanest.

### BUG-004 · `/ready` does not actually validate the outbox table · **P0** · `confirmed`
- **Where:** `services/erp/src/api/health.py:32-62`.
- **What's wrong:** the route docstring (line 7) and wave doc (line 64 of `WAVE_0_FOUNDATIONS.md`) promise "checks DB + outbox + Kafka". The implementation only runs `SELECT 1` against the DB and then **hardcodes** `"status": "ok", "note": "no outbox poller yet (W0)"` for both outbox and Kafka. There is no `SELECT 1 FROM outbox_events LIMIT 1` (or even a check that the table exists). If the outbox migration is missing, the orchestrator's readiness probe will still report 200 and route traffic to a service that cannot publish. The "informational until W1" comment is fine, but the W0 contract should be honest: don't call it "checks" if it doesn't check.
- **Fix:** either drop the `outbox` and `kafka_producer` keys from the response until W1, or run `await session.execute(text("SELECT to_regclass('public.outbox_events')"))` and only mark `"status": "ok"` if the result is non-NULL.

### BUG-005 · `permissions` migration declares composite PK `(id, key)` but ORM model declares `key` as sole PK · **P0** · `confirmed`
- **Where:** migration `services/erp/migrations/versions/0100_identity.py:61-70` vs model `services/erp/src/identity/models.py:99-108`.
- **What's wrong:** the migration has
  ```python
  sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, ...),
  sa.Column("key", sa.String(128), primary_key=True),
  ```
  — composite primary key on `(id, key)`. The model has
  ```python
  class Permission(Base):
      key: Mapped[str] = mapped_column(String(128), primary_key=True)
  ```
  — single-column PK on `key`. After `alembic upgrade head` the DB has `(id, key)` PK; any `session.get(Permission, "erp.so.write")` or `session.merge(...)` will fail because SQLAlchemy expects `key` to be the only PK and will not supply `id`. The catalog read at `identity/api.py:45` (`select(Permission).order_by(...)`) will work, but any code that does `session.get(Permission, key)` will hit `InvalidRequestError`. This is a latent landmine for W1+ when the first write path touches `Permission`.
- **Fix:** in the migration, drop the `id` column and the `primary_key=True` flag on it (or set `id` to `unique=True, nullable=False` instead). The model is correct: `key` is the natural PK for a global catalog.

### BUG-006 · Service accounts are granted *all* permissions unconditionally · **P0** · `confirmed`
- **Where:** `services/erp/src/identity/service.py:90-93`.
- **What's wrong:** `if principal.is_service_account: return True` — any flag set in JWT `is_service_account` claim (or, in W0, any DB row with `is_service_account=true`) bypasses the entire permission catalog. A single compromised service-account token can do any operation the API exposes. The wave summary acknowledges this is a placeholder for W6, but as shipped it is a privilege-escalation vector against any future endpoint.
- **Fix (minimal W0):** require an explicit allow-list on the principal: `if principal.is_service_account: return permission_key in principal.service_account_scopes`. Then `Principal` carries `service_account_scopes: frozenset[str] = frozenset()`. Default scope is empty (deny). The full W6 work can then add the per-tenant allow/deny list.

### BUG-007 · `ObservabilityMiddleware` resolves tracer in `__init__` before `init_tracing()` runs in lifespan · **P0** · `confirmed`
- **Where:** `services/erp/src/observability/middleware.py:40` (middleware init) vs `services/erp/src/api/main.py:61` (lifespan calls `init_tracing`).
- **What's wrong:** `self._tracer = trace.get_tracer(service_name)` is called when the middleware is constructed — which is *before* `app.router.lifespan_context` runs `init_tracing()`. Until lifespan fires, `trace.get_tracer` returns a no-op proxy tracer. The first request after boot (which often arrives before the lifespan has fully completed in test setups that bypass lifespan) will produce no spans. Once lifespan does run and calls `trace.set_tracer_provider(provider)`, the previously-obtained tracer reference is *still* a proxy in some OTel SDK versions (depends on the version pinned in `pyproject.toml:27-29` — `>=1.27`). On top of that, BUG-022 below means we don't even go through the `observability.tracing.tracer()` helper.
- **Fix:** use `observability.tracing.tracer(service_name)` (which goes through the singleton provider) and resolve the tracer per-request inside `dispatch()` rather than caching in `__init__`. Or, in `__init__`, call `init_tracing(service_name=service_name)` so the provider is set before any request hits.

### BUG-008 · Tenant-id is sometimes `UUID`, sometimes `str`, sometimes `Any`; same value passes through 4 type systems · **P0** · `confirmed`
- **Where:**
  - `services/erp/src/observability/middleware.py:54-56` — `request.state.tenant_id = request.headers.get("x-tenant-id")` (raw `str | None`)
  - `services/erp/src/shared/tenant.py:18` — `return getattr(request.state, "tenant_id", None)` annotated `-> UUID | None` (lies)
  - `services/erp/src/shared/idempotency.py:227-228` — `tenant_id_str = current_tenant_id(request); if tenant_id_str is None:` (treats as `str | None`)
  - `services/erp/src/shared/idempotency.py:234` — `tenant_id = UUID(str(tenant_id_str))` (coerces)
  - `services/erp/src/identity/deps.py:35` — `tenant_id: UUID = Depends(require_tenant_id)` (Pydantic coercion)
  - `services/erp/src/identity/api.py:62, 92` — `tenant_id = request.state.tenant_id` (treats as `str`; compared against `Role.tenant_id` which is `UUID`)
- **What's wrong:** there are *three* sources of truth for the current tenant and they disagree on type. SQLAlchemy happens to coerce `str` to `UUID` in the WHERE clause (because the column is `PG_UUID(as_uuid=True)`), but this is an accident. The dead `tenant_id_str is None` branch in idempotency.py can never fire in practice because `require_tenant_id` raises first. A malformed UUID in the header leaks through to a 422 from Pydantic, not a clean 400. Tests that pass `x-tenant-id: <valid-uuid-string>` work, but the type system is lying.
- **Fix:** pick one. Either (a) make `observability.middleware.py` parse to `UUID` once and store the `UUID` (or `None`) on `request.state.tenant_id`, or (b) make `current_tenant_id` a function that parses and returns `UUID | None` and cache the result. Update annotations. Drop the `UUID(str(tenant_id_str))` re-coercion in idempotency.

---

## P1 — Wrong / fragile

### BUG-009 · `require_permission` does not delegate to `PermissionService.assert_can` · **P1** · `confirmed`
- **Where:** `services/erp/src/identity/deps.py:69-88`.
- **What's wrong:** the dep imports `PermissionService` but never uses it; it replicates the `is_service_account` short-circuit and the frozenset check inline, with its own copy of the `metrics().permission_denials_total.inc()` call. BUG-006 (service-account = all perms) is bypassed in this path: the dep will deny a service account that lacks the key in `permission_keys` even though `PermissionService.can` would say yes. Behavior diverges depending on which gate runs.
- **Fix:** call `await PermissionService(session).assert_can(principal, permission_key)` (open a short-lived session in the dep, or pass the request session if available). Delete the inline logic. The metrics increment is in the service already.

### BUG-010 · Dead `from identity.service import PermissionDenied` import in `require_permission` · **P1** · `confirmed`
- **Where:** `services/erp/src/identity/deps.py:72`.
- **What's wrong:** `from identity.service import PermissionDenied as _PD  # avoid circular` is imported, never referenced, and the comment is misleading (the import is not needed to break a cycle — `identity.service` is already imported at the top of the file). Lint rule `F401` should flag this; ruff config has `select = ["E", "F", ...]` (line 77) so this would fail `ruff check`. Verified it slipped past CI because the import is inside a function, which the existing ruff config may not lint by default. Either way: dead code.
- **Fix:** delete line 72.

### BUG-011 · `IdempotencyMiddleware._is_idempotent_path` always returns `False` · **P1** · `confirmed`
- **Where:** `services/erp/src/shared/idempotency.py:283-287`.
- **What's wrong:** dead method. The whole opt-out branch (lines 215-218) is unreachable. It exists only to satisfy a hook the W0 docstring promises.
- **Fix:** either implement the path-based allow-list (e.g. `("/api/v1/erp/identity/permissions",)` — GETs don't reach here anyway because `MUTATING_METHODS` filters them) or delete the method and the call site.

### BUG-012 · `dump_openapi.py` writes to a gitignored path · **P1** · `confirmed`
- **Where:** `services/erp/scripts/dump_openapi.py:34` writes to `docs/openapi/erp.json`; `docs/openapi/.gitignore:3` excludes `erp.json`.
- **What's wrong:** the script's default output is committed in the wave summary and `docs/openapi/README.md` as a contract artifact, but the directory's `.gitignore` actively excludes it. CI has no step that runs `erp-v2-dump-openapi` (`.github/workflows/ci.yml` only runs ruff, mypy, pytest, and the audit-link check). So `erp.json` is never produced, never committed, and the "merge blocked on drift" guarantee in `docs/openapi/README.md:13` cannot be enforced.
- **Fix:** pick one. Recommended: (a) delete the `.gitignore` entry, (b) add a CI step `services/erp && erp-v2-dump-openapi && git diff --exit-code docs/openapi/erp.json`. Alternatively (b'): change the script's default to `--out -` (print to stdout) and let CI fail on a comparison of two `git show` invocations.

### BUG-013 · `app.state.metadata` is set but never read · **P1** · `confirmed`
- **Where:** `services/erp/src/api/main.py:107`.
- **What's wrong:** `app.state.metadata = Base.metadata` and the comment claims it is "for tooling that needs it (e.g. Alembic env)". The Alembic env (`services/erp/migrations/env.py:24`) imports `from shared.db import Base` directly — it never touches `app.state.metadata`. No other code reads it. Dead state.
- **Fix:** delete the line and the comment. If the intent is to expose metadata for tooling, add a `make_app_metadata()` accessor and document it; right now it is misleading.

### BUG-014 · `init_metrics` is not idempotent across registry choices · **P1** · `confirmed`
- **Where:** `services/erp/src/observability/metrics.py:80-85`.
- **What's wrong:** the docstring says "Idempotent init". It is idempotent in the *count* of calls (subsequent calls return the same `Metrics` instance), but if the first call passes `registry=None` and the second passes `registry=custom_registry`, the second registry is silently ignored — the metrics are still bound to the default `REGISTRY`. Tests that pass a custom registry via `create_app_v2()` to isolate Prometheus state will get a global-registry singleton instead and the test fixture's expectation breaks. The hint in the task was right that there is a real problem here, but the wording was off: it's "silently ignores" rather than "silently overwrites".
- **Fix:** either (a) raise on registry mismatch if `_default is not None and _default.registry is not registry`, or (b) make `init_metrics` a strict one-shot (raise if called twice with non-None args), or (c) make `metrics()` accept a registry argument each call. Pick (a) for clarity.

### BUG-015 · `testcontainers[postgres]` import fails on systems without Docker or the `docker` package · **P1** · `confirmed`
- **Where:** `services/erp/pyproject.toml:55` (`testcontainers[postgres]>=4.8`); `services/erp/tests/conftest.py:82-86` imports it inside a `try/except ImportError`.
- **What's wrong:** `testcontainers` imports the `docker` package at module load. On a system where `docker` is not installed (e.g. a CI runner, a developer's laptop without Docker Desktop), `from testcontainers.postgres import PostgresContainer` raises `ImportError`, which conftest catches and skips. Fine — except `testcontainers[postgres]>=4.8` *also* tries to import the `testcontainers.core` package, which itself imports `docker` *unconditionally* at the top of `__init__.py`. The conftest's `try/except ImportError` is around the submodule import, but the *top-level* `import testcontainers` (implicit via `from testcontainers.postgres import …` which is a submodule) may not actually fail on a clean Python — only the attribute access might. Net result: in some envs the skip works, in others the test collection crashes with a confusing `ModuleNotFoundError: No module named 'docker'` *before* the skip guard. Pin the lower bound: `testcontainers[postgres]>=4.0,<4.8` (the older major doesn't pull `docker` at import), or add a separate `testcontainers-core` extra and require it only in `[test]` not `[dev]`.
- **Fix:** split `[dev]` into `[dev]` (linter/test-runner only) and `[test-infra]` (testcontainers + faker), and require `[test-infra]` only in CI / conftest under an explicit `pytest --requires-docker` marker. Or pin a testcontainers version whose top-level import does not require `docker`.

### BUG-016 · OTel contextvars (`request_id_var`, `tenant_id_var`, etc.) are not reset between tests · **P1** · `confirmed`
- **Where:** `services/erp/tests/conftest.py:118-131` (`client` fixture); the variables are module-level in `services/erp/src/observability/logging.py:20-23`.
- **What's wrong:** the `client` fixture does not reset the four `ContextVar`s defined in `observability/logging.py`. The `ObservabilityMiddleware` does call `request_id_var.reset(token_rid)` and `tenant_id_var.reset(token_tid)` (lines 99-100), but `trace_id_var` and `span_id_var` are set *without* tokens and never reset (lines 75-78). A test that asserts a log record's `trace_id` (e.g. via a captured structlog handler) will see the previous test's trace_id leak through. The `client` fixture is function-scoped, so this matters for any test that runs in non-default order.
- **Fix:** add an autouse fixture in `conftest.py` that does
  ```python
  from observability.logging import request_id_var, span_id_var, tenant_id_var, trace_id_var
  yield  # then reset
  for v in (request_id_var, span_id_var, tenant_id_var, trace_id_var):
      v.set(None)
  ```
  or, better, in `ObservabilityMiddleware.dispatch`, use `set()` for `trace_id_var`/`span_id_var` too and `reset()` them in the `finally`.

### BUG-017 · Idempotency middleware commits on its own session; races with the route's transaction · **P1** · `confirmed`
- **Where:** `services/erp/src/shared/idempotency.py:178` (`await session.commit()` inside `DbIdempotencyStore.put`).
- **What's wrong:** the store opens a *new* `AsyncSession` and commits the cached response independently of the route's own session. Sequence: (1) route handler does `INSERT … COMMIT` in its session; (2) middleware then opens a *second* session and `INSERT`s the idempotency record + commits. If the second commit fails after the first succeeded (DB connection drop, transient error), the route's mutation is durable but the idempotency cache is empty — a retry replays the mutation. Conversely, if the route raises *after* the middleware persists (the code at line 253 runs the handler *first*, so this is theoretical, but the design has the bug latent), the cache will point to a non-existent state.
- **Fix:** persist the idempotency record *inside* the route's session, by passing the session through a request-scoped state (e.g. `request.state.db_session`) populated by the route's `get_db_session` dep. Or, at minimum, accept a session argument in `IdempotencyStore.put(..., session=...)`.

### BUG-018 · `PermissionService.load_principal` is N+1 on permissions · **P1** · `confirmed`
- **Where:** `services/erp/src/identity/service.py:73-80`.
- **What's wrong:** the loop `for role in user.roles: ... RolePermission.role_id == role.id` issues one query per role. A user with N roles triggers N+1 queries (1 for the user, N for permission sets). `Role.permissions` is declared with `lazy="selectin"` (line 93-96 of models.py) — the inner query is the secondary one. For typical W0 users (1-3 roles) this is fine; for a power user with 10+ roles the cost is noticeable. The `_load_keys` helper (lines 123-131) is the correct single-query version, but `load_principal` doesn't use it.
- **Fix:** use the same join pattern as `_load_keys` (single SELECT joining `User → UserRole → Role → RolePermission`) to compute the union in one round-trip. Drop the loop.

### BUG-019 · `identity/api.py` uses `request.state.tenant_id` instead of `Depends(require_tenant_id)` · **P1** · `confirmed`
- **Where:** `services/erp/src/identity/api.py:62, 92`.
- **What's wrong:** `list_roles` and `get_role` pull `tenant_id` from `request.state.tenant_id` (a raw header string, see BUG-008) rather than injecting it via the dep. The `require_permission` dep *does* pull the principal (which transitively calls `require_tenant_id`), so the dep chain has validated it, but the route body then re-reads the same value from a different source. A header manipulation between the dep call and the handler call is not possible in practice, but the code lies about its dependency contract and complicates refactors.
- **Fix:** add `tenant_id: UUID = Depends(require_tenant_id)` to the route signatures and use that.

### BUG-020 · `test_permission_service.py` uses `MagicMock` for the session and only tests the no-DB paths · **P1** · `confirmed`
- **Where:** `services/erp/tests/unit/test_permission_service.py:30, 38, 45, 59, 68`.
- **What's wrong:** the suite asserts on `can()` and `assert_can()` for principals with **pre-fetched** `permission_keys` — both of which short-circuit before hitting `self._session.execute()`. `load_principal` (the path that *does* query the DB) is never unit-tested; if it were, the MagicMock would explode because `await session.execute(stmt)` is not configured. This leaves the most complex method in the service uncovered.
- **Fix:** add a unit test using an in-memory `AsyncMock` with a real-ish `MagicMock` chain (`session.execute.return_value.scalar_one_or_none.return_value = fake_user; user.roles = [...]`), or convert it to an integration test using the `pg_session_factory` fixture. Either is fine; the current gap is the real bug.

### BUG-021 · `list_permissions` is unauthenticated and exposes the full catalog · **P1** · `confirmed`
- **Where:** `services/erp/src/identity/api.py:40-49`.
- **What's wrong:** the route has no `Depends(get_current_principal)`, no rate limit, and no `require_permission`. Any internet caller can enumerate every permission key the ERP service will ever check (`erp.so.cancel`, `finance.period.close`, `platform.tenant.write`, etc.). This is the textbook definition of an information disclosure that helps an attacker plan privilege escalation against future endpoints. The wave doc claims the catalog is "global, not tenant-scoped" as if that's a permission to be unauthenticated; the SPEC §2 ("RBAC — every mutating route") doesn't override this, but a list endpoint should still require a valid `x-user-id` / `x-tenant-id` pair at minimum.
- **Fix:** add `dependencies=[Depends(require_permission("identity.role.read"))]` (or at least `Depends(get_current_principal)`) to `list_permissions`.

### BUG-022 · `ObservabilityMiddleware` calls `trace.get_tracer()` directly, bypassing `observability.tracing.tracer()` · **P1** · `confirmed`
- **Where:** `services/erp/src/observability/middleware.py:9` (import) and `services/erp/src/observability/middleware.py:40` (use).
- **What's wrong:** the project has a thin wrapper `observability.tracing.tracer(name)` (lines 53-55) whose docstring says "Get a tracer without re-initialising. Safe to call from any module." — but the middleware imports `from opentelemetry import trace` and calls `trace.get_tracer(service_name)` directly. The wrapper exists for a reason (single place to swap the SDK, test seam) and we are not using it. Combined with BUG-007, the middleware's tracer is a proxy until lifespan runs and the proxy is not always wired through to the live provider.
- **Fix:** `from observability.tracing import tracer` and `self._tracer = tracer(service_name)` (or per-request resolution — see BUG-007).

---

## P2 — Cosmetic / dead code / doc drift

### BUG-023 · `services/erp/README.md` "Current status" still says W0 not started · **P2** · `confirmed`
- **Where:** `services/erp/README.md:33-35`.
- **What's wrong:** the text reads `W0 not started. src/app.py raises NotImplementedError…`. `app.py` actually does `from api.main import create_app_v2; app = create_app_v2()` — it does not raise. The wave is done. The root `README.md` table (lines 44-54) has the same stale text.
- **Fix:** update both status tables to `W0 done`. Mark the rest as `not started`. Optionally add a per-row "shipped" date.

### BUG-024 · `__init__.py` files use the word "placeholder" · **P2** · `confirmed`
- **Where:** `services/erp/src/__init__.py:1` — `"""ERP v2 — placeholder package init."""`.
- **What's wrong:** the only "placeholder" / "TODO" / "FIXME" string in the entire services tree is this one docstring. Cosmetic, but a grep for "placeholder" should not match the shipped code.
- **Fix:** replace with the actual description (the existing prose in the same file is fine — drop "placeholder"). Same audit applied to `services/erp/src/api/__init__.py`, `services/erp/src/identity/__init__.py`, `services/erp/src/observability/__init__.py`, `services/erp/src/shared/__init__.py` — they are all docstring-only and need no change beyond a re-read for stale "placeholder"-style language.

### BUG-025 · `docs/openapi/.gitignore` contradicts `docs/openapi/README.md` · **P2** · `confirmed`
- **Where:** `docs/openapi/.gitignore:1-4` vs `docs/openapi/README.md:5-13`.
- **What's wrong:** the README says the file is "committed to the repo" and CI "merge blocked on drift"; the `.gitignore` says `erp.json` is excluded. They cannot both be right. This is the same root cause as BUG-012 but at the doc level.
- **Fix:** when fixing BUG-012, also reconcile these two files (delete the `.gitignore` line, or rewrite the README).

### BUG-026 · RLS test reads in the same session as the writes but resets GUC per `SET LOCAL` · **P2** · `confirmed`
- **Where:** `services/erp/tests/integration/test_rls_isolation.py:22-50`.
- **What's wrong:** the test sets `app.tenant_id` to A, inserts A, then re-sets to B, inserts B, commits. After the commit, subsequent reads open *new* sessions and re-set the GUC. The hint flagged that the test "doesn't await session.commit after the first batch insert" — actually it does at line 38. The remaining real issue is: the test does not assert that *before* commit, switching the GUC and re-reading the same session's pending state works (or fails — depends on Postgres visibility rules). This is a coverage gap, not a bug in the test as written, but the test name suggests isolation testing and the actual isolation assertion is weaker than it could be.
- **Fix:** add a third assertion: in one session, set GUC to A and confirm `SELECT count(*) FROM users` returns 1; then set GUC to B in the *same* session and confirm the same `SELECT` returns 1 (not 2). This catches a Postgres-level regression where the RLS predicate is not re-evaluated on GUC change.

### BUG-027 · `pgcrypto` extension created but never used · **P2** · `confirmed`
- **Where:** `services/erp/migrations/versions/0100_identity.py:29`.
- **What's wrong:** `op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')` is created but no code references `gen_random_uuid()` (we use `uuid_generate_v4()` from `uuid-ossp`). The extension is an extra privilege, an extra upgrade risk, and extra startup time for no benefit.
- **Fix:** delete the line. If a future wave needs it, add it then.

### BUG-028 · `ApiError.__init__` double-passes message · **P2** · `confirmed`
- **Where:** `services/erp/src/shared/errors.py:44-52`.
- **What's wrong:** `super().__init__(message)` stores the message in `args`; `self.message = message` stores it as an attribute. Both work; the handler reads `exc.message` (line 107). This is intentional and not a bug, but the wave hint flagged it for review. Verdict: keep — the duplication is the cleanest way to make the attribute available across both `Exception.args` consumers and direct attribute access. No change.

### BUG-029 · Wave-1+ scaffolding directories have no `__init__.py` · **P2** · `confirmed`
- **Where:** `find services/erp/src -type d -empty` returns 47 directories under `ai/`, `crm/`, `finance/`, `hr/`, `legal/`, `master-data/`, `o2c/`, `p2p/`, `platform/`, `trade/`. None have `__init__.py`.
- **What's wrong:** Python won't recognize these as packages until the wave that fills them adds an `__init__.py`. If anything does `from o2c import …` in W0, the import fails. Nothing in W0 does — but the scaffolding creates a footgun for W1, where a developer assumes "the dir exists, the import will work". Also, ruff `select = ["E", "F", ...]` doesn't flag missing `__init__.py` by default; the directories are invisible to tooling.
- **Fix:** add empty `__init__.py` to each wave-1+ scaffolding directory now, or wait for the wave that owns it (current convention is the latter, which is fine — just document it).

### BUG-030 · `check_coverage.py` uses `str(d.call)` for dependency matching · **P2** · `confirmed`
- **Where:** `services/erp/scripts/check_coverage.py:33, 64`.
- **What's wrong:** `deps = [str(d.call) for d in route.dependant.dependencies]` then `if "require_permission" in d` is doing substring matching against the `repr` of a callable. `str(callable)` returns something like `<function require_permission at 0x7f…>` — the function's qualified name shows up only if the closure hasn't been wrapped. With FastAPI's dependency caching, the actual call object is a `Depends()` marker, and `str()` of it varies by Python version. The matcher will work for the happy case today, but is one refactor away from silently false-negative.
- **Fix:** use `d.call.__qualname__` or walk `route.dependant.dependencies[*].call` and check `call.__name__` (or `call.__qualname__`). Better: expose a custom dep-marker base class and `isinstance(d.call, _HasPermission)` for an exact match.

---

## Cross-references: hints from the task that did NOT pan out as bugs

These were called out in the audit brief but, on close reading, are not actually broken. Recording the verdict so the next reviewer doesn't re-investigate.

| Hint | Verdict | Why it's fine |
|------|---------|---------------|
| `idempotency.py:IdempotencyMiddleware` reads body once — verify Pydantic can still bind | **Not a bug** | Starlette's `Request.body()` caches its result on first read; subsequent calls return the same bytes. FastAPI's parameter binding reads from that cache. |
| `identity/api.py:80` duplicate import of `request` in `get_role` | **Not a bug** | `request: Request` is a *parameter type annotation*, not an import. There is no `import request` anywhere. The function-local `from uuid import UUID as _UUID` is a deliberate local import to avoid carrying `UUID` at module scope. |
| `identity/service.py:load_principal` queries User with User.roles via `selectinload` | **Not a bug** | `User.roles` has `lazy="selectin"` (line 60 of `models.py`), which makes SQLAlchemy emit the secondary load automatically. The explicit `selectinload(User.roles)` is not needed. (N+1 still exists — see BUG-018 — but the join is correct.) |
| `shared/errors.py` validation handler uses `exc.errors()` and references `ValidationFailedError` inside a closure | **Not a bug** | `ValidationFailedError` is defined at module scope before `install_error_handlers` is called, so the name resolves at handler-invocation time. The closure captures the name, not the value, but that's fine because the name is module-global. |
| `shared/idempotency.py:155` `route_label = request.url.path` initialized before `call_next` | **Not a bug** | The init is to a fallback; line 81 re-assigns after `call_next` returns. Verified. |
| `api/main.py:96-97` middleware order in `add_middleware` | **Not a bug** | `add_middleware` inserts at the front of the user-middleware stack, so the last added is the *outermost*. The comment is right: observability wraps idempotency. The order is correct. |
| `migrations/versions/0100_identity.py:64` `op.bulk_insert` `description` column is `Text` not `String` | **Not a bug** | The `sa.table(...)` in the bulk_insert declares each column with the same type as the create_table; `Text` → `Text`. No dialect-specific quirk. |
| `tests/integration/test_health.py` import of `text` from sqlalchemy | **Not a bug** | The file doesn't import `text` at all; only `pytest` is imported. No shadowing. |
| `test_rls_isolation.py` doesn't `await session.commit` after the first batch insert | **Not a bug** | Line 38 commits both inserts together. The two reads (lines 41-50) happen in fresh sessions, which is the correct isolation pattern. |
| `test_permission_service.py` uses MagicMock for the session | **Partial (see BUG-020)** | MagicMock works for `can()` / `assert_can()`; the suite just doesn't exercise `load_principal`. That's a coverage gap, not a test failure. |
| All `__init__.py` files were turned into docstring-only modules — verify no downstream code expected re-exports | **Not a bug** | Every W0 import uses the explicit submodule path (e.g. `from identity.models import User`). No code does `from identity import User` (which would have worked if the `__init__.py` re-exported). |
| Look for any import that could fail at import time on a clean system | **Not a bug** | All imports are inside try/excepts or function bodies. The exception is `testcontainers` covered by BUG-015. |
| Look for any TODO/FIXME/XXX/placeholder strings I left | **Not a bug** | Only one match: `services/erp/src/__init__.py:1` docstring "placeholder package init" — covered as BUG-024. |

---

## Quick-fix priority (for the W0 hotfix PR)

If the goal is to land the smallest diff that makes the W0 deliverable correct, this is the order I'd batch:

1. **BUG-001 + BUG-002** (one migration) — `idempotency_keys` table with proper UUID column
2. **BUG-003** — fix `set_tenant_context(None)` so RLS doesn't blow up
3. **BUG-005** — drop `id` PK from the `permissions` table
4. **BUG-007 + BUG-022** — route the middleware through `observability.tracing.tracer()` and call `init_tracing()` in `__init__`
5. **BUG-008** — settle the tenant-id type contract
6. **BUG-019** — use `Depends(require_tenant_id)` in `identity/api.py`
7. **BUG-012 + BUG-025** — reconcile `docs/openapi/.gitignore` and add the CI step
8. **BUG-016** — add the autouse contextvar-reset fixture

Everything else is a separate PR.
