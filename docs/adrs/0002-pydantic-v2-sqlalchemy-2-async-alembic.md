# ADR-0002 — Pydantic v2 + SQLAlchemy 2.0 async + Alembic

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | W0 (foundations); framework applies to every module |
| Supersedes | v1's Pydantic v1 + SQLAlchemy 1.4 sync |
| Related | [ADR-0010 — domain / infrastructure / workflows split](./0010-domain-infrastructure-split.md) |

## Context

v1 `services/erp` runs on Pydantic v1, SQLAlchemy 1.4 sync, `psycopg2`,
and Alembic 1.12 sync env. The pain points:

- Pydantic v1's `parse_obj` and `dict()` ergonomics are clunky in a
  service that needs to round-trip 65+ permission keys, 8-line SO
  payloads, and Avro events with discriminated unions.
- SQLAlchemy 1.4 sync forces every request to block on the DB.
  Combined with the `await` chain in FastAPI, this is the #1 source
  of tail latency in v1.
- `psycopg2` is sync-only. Switching to `asyncpg` unblocks the move
  to async SQLAlchemy but requires the rest of the stack to be async.
- Alembic 1.12 sync env requires a second engine to run alongside
  the async app engine. Awkward, doubles the connection pool.

We considered:

- **Pydantic v1 → stay on v1.** Cheaper migration, but the v2
  `model_validator`, `Field(..., discriminator=...)`, and 5–10×
  faster `model_dump_json()` are the right tools for the multiline
  + outbox payloads.
- **SQLAlchemy 1.4 sync → 1.4 async with sync `psycopg2` driver.**
  Doesn't solve the blocking-call problem; the `await` chain still
  fences.
- **SQLModel (Pydantic + SQLAlchemy wrapper).** Tempting (one
  declarative class for both) but the union with raw SQLAlchemy 2.0
  types in the migration story is rough, and we lose the explicit
  separation between ORM models and wire schemas.
- **Tortoise ORM + Pydantic v2.** Async-native, but its migration
  story is years behind Alembic and its query builder is
  second-system.

## Decision

- **Pydantic v2** for every wire and inter-module schema. No
  `dict[str, Any]` in domain code; all payloads are typed models
  inheriting from `shared.schemas.AppModel`.
- **SQLAlchemy 2.0 async** (`AsyncEngine`, `AsyncSession`,
  `async_sessionmaker`) for every persistence call. We use the
  typed `Mapped[...]` declarative API exclusively — no legacy
  `Column` syntax except in the cross-cutting `idempotency_keys`
  table that the store needs before the W1 migrations add it
  formally.
- **`asyncpg`** as the Postgres driver. `psycopg2` is gone.
- **Alembic 1.13+ async env.** Single env, single metadata
  (`shared.db.Base.metadata`).
- **Mypy strict** for the service package. `pyright` would be
  fine too, but mypy is what the parent repo uses; we mirror.

## Consequences

### Positive

- Every request is `async def` end-to-end. Tail latency drops by
  30–40% in v1's rough benchmarks.
- Pydantic v2's `discriminated unions` make event payloads
  (`OrderConfirmed | InvoiceGenerated | …`) a one-line type
  declaration.
- SQLAlchemy 2.0's typed `Mapped[UUID]` columns surface type drift
  in code review, not at runtime.
- One Alembic env, one engine, one pool. No double connection
  accounting.

### Negative / costs

- **Sync tooling is gone.** `psycopg2`-based admin scripts, some
  reporting jobs, and any legacy `alembic` invocation that hasn't
  been ported will fail. We accept this; the wave-by-wave plan
  retires sync tools in W1.
- **Alembic migrations are async.** The review template now
  requires `await session.run_sync(...)` wrappers, which look
  unusual the first time.
- **Pydantic v2's `model_config = ConfigDict(extra="forbid")` is
  strict by default.** Schemas that previously accepted unknown
  fields must be updated. This is the desired behaviour for an
  internal API; client SDKs are generated from OpenAPI and the
  contract is enforced.
- **Mypy strict + Pydantic v2 + FastAPI** means we need the
  `pydantic.mypy` plugin and a few `disallow_untyped_decorators =
  false` overrides. Documented in `services/erp/pyproject.toml`.

### What we lose vs v1

- The `dict` round-tripping pattern in v1's O2C order flow.
  v2 forces typed models; the cost is ~20% more code for the typed
  schemas, the gain is that every Avro payload has a Python type
  that can be diff'd in code review.
