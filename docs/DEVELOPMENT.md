# Development — Local dev workflow

> **Audience:** anyone with a W0-vintage clone who is about to write
> their first line of v2 code.
> **Companion docs:** [`GETTING_STARTED.md`](./GETTING_STARTED.md) (5-min
> boot), [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md) (when things break).

This doc covers the day-to-day loop: branch naming, where code lives,
how to add a new module, how to add a migration / permission / router,
the merge-back flow, and the coverage guard.

## Branch naming

Every wave lands on a short-lived branch off this repo's `main`,
then merges back into `main` *here* before mirroring up to the
parent (`cautious-goggles/feat/erp-crm`).

```
feat/erp-v2-wN-<short-kebab>
```

Examples (per [`MERGE_PLAN.md`](../MERGE_PLAN.md)):

```
feat/erp-v2-w0-foundations
feat/erp-v2-w1-o2c-complete
feat/erp-v2-w2-p2p-complete
feat/erp-v2-w3-master-data
...
```

Rules:

- One branch per wave. A wave PR may contain many commits; the
  branch is squashed on merge.
- Fix-only branches (e.g. `fix/erp-v2-w0-rls-policy-bug`) are
  fine, but they rebase onto the wave branch and ship with the
  wave.
- Never push directly to `main` here. The branch is a force of
  habit so CI can run on every PR.

## Where the code lives

```
services/erp/
├── pyproject.toml
├── migrations/                   # Alembic (async env)
│   ├── env.py
│   └── versions/
│       ├── 0100_identity.py
│       └── 0101_identity_rls.py
├── scripts/                      # CLI entry points
│   ├── dump_openapi.py
│   └── check_coverage.py
├── tests/                        # unit + integration + contract
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── contract/                 # W10
└── src/
    ├── app.py                    # uvicorn entry
    ├── api/                      # cross-cutting (factory + health)
    │   ├── main.py
    │   └── health.py
    ├── shared/                   # cross-cutting (errors, schemas, db, idempotency, tenant)
    ├── observability/            # OTel + Prometheus + structured logging
    ├── identity/                 # reference implementation (W0)
    ├── o2c/                      # empty until W1
    ├── p2p/                      # empty until W2
    ├── master-data/              # empty until W3
    ├── finance/                  # empty until W4
    ├── crm/                      # empty until W5
    ├── hr/                       # empty until W8
    ├── legal/                    # empty until W8
    ├── trade/                    # empty until W9
    ├── platform/                 # empty until W6
    └── ai/                       # empty until W7
```

Every Python package under `src/` follows the inner layout from
[`ADR-0010`](./adrs/0010-domain-infrastructure-split.md):

```
<module>/
├── api.py                # FastAPI router (mounted by api/main.py)
├── service.py            # business rules
├── models.py             # SQLAlchemy 2.0 async ORM
├── schemas.py            # Pydantic v2 wire models
├── domain/
├── infrastructure/
├── workflows/            # Temporal sagas
├── consumers/            # Kafka consumers
└── outbox/               # outbox writers
```

In W0, `identity/` is the reference implementation; later modules
fill in the rest of the inner layout as their wave ships.

## How to add a new module

A new module ships in a wave. Example: W1 adds
`o2c/sales-order/`. The shape of the work:

1. **Pick a wave.** Read [`SPEC.md`](./SPEC.md) for the wave's
   scope. The wave PR's body uses the
   [`MERGE_PLAN.md` parent-PR template](../MERGE_PLAN.md#per-wave-pr-template).
2. **Create the package.** Empty directories under
   `services/erp/src/<parent>/<module>/` with the standard
   inner layout. `__init__.py` re-exports the public surface.
3. **Add the router.** The router goes in `<module>/api.py`. The
   convention is one module-local `router = APIRouter(...)` per
   `api.py`; the wave PR adds the
   `app.include_router(<module>.api.router, prefix=...)` call
   **inside `<module>/api.py`** so the boot path stays clean
   until the wave is ready. (See
   [`ADR-0010`](./adrs/0010-domain-infrastructure-split.md#consequences)
   for why `api/main.py` does *not* import module routers.)
4. **Add the migration.** See the next section.
5. **Add the permission keys.** See "How to add a new permission"
   below.
6. **Add tests.** Unit + integration + (W10) contract. The
   parent-PR template requires happy path + 1 auth-failure +
   1 idempotency-replay.
7. **Open the PR** against this repo's `main`. Title:
   `feat(erp-v2): wave N — <name>`. Body: link to the SPEC
   section, list of audit items closed, parent-PR template
   filled in.
8. **After merge here**, open the parent PR against
   `cautious-goggles/feat/erp-crm` (see "Merge-back flow"
   below).

## How to add a new migration

Alembic, async env, RLS convention.

1. **Create the revision file:**

   ```bash
   cd services/erp
   alembic revision -m "<area> <what>"
   # produces migrations/versions/<rev_id>_<slug>.py
   ```

   Filename convention: `NNNN_<area>_<what>.py` where `NNNN`
   is the next four-digit number after the highest existing
   revision. Examples: `0102_outbox.py`, `0200_o2c_sales_order.py`.

2. **Fill in `upgrade()` and `downgrade()`.** Every migration
   is reversible. The async env uses
   `op.execute("...")` for raw SQL (RLS, GUCs) and
   `op.create_table(...)` for declarative.

3. **RLS convention.** Tenant-scoped tables get RLS in a
   follow-up migration (not in the table-create), so the
   table and the policy are reviewable as separate units.
   The pattern (see `migrations/versions/0101_identity_rls.py`):

   ```python
   def upgrade() -> None:
       for table in _TENANT_TABLES:
           op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
           op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
           op.execute(f"""
               CREATE POLICY {table}_tenant_isolation ON {table}
               USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
               WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
           """)
   ```

   The `app.tenant_id` GUC is set by the
   `shared.db.set_tenant_context()` call inside each request
   session.

4. **`deleted_at`.** Every domain table gets
   `deleted_at TIMESTAMPTZ NULL` per
   [`ADR-0007`](./adrs/0007-soft-delete.md). The
   `DeletedFilter` helper (W1) and the RLS policy's
   `USING (deleted_at IS NULL)` clause enforce it.

5. **Indexing.** Include the indexes your queries need
   *in the create-table migration*. A
   `CREATE INDEX CONCURRENTLY` later is a deploy-time
   coordination problem; the create-time index is free.

6. **Test the round trip.** `alembic upgrade head` then
   `alembic downgrade -1` then `alembic upgrade head` again.
   CI runs the round trip on every PR.

## How to add a new permission

A new permission key has to land in **two** places, in this order:

1. **The global catalog** — in
   `migrations/versions/0100_identity.py` (or a follow-up
   migration to it; never edit a shipped migration in a
   later wave). Add a row to the `op.bulk_insert(...)` call
   with a stable `key`, a `resource`, an `action`, and a
   one-line `description`. Example pattern (key
   `o2c.so.cancel`):

   ```python
   ("o2c.so.cancel", "sales_order", "cancel",
    "Cancel a confirmed sales order"),
   ```

   The key is the dotted string every handler passes to
   `require_permission(...)`. The convention is
   `<area>.<resource>.<action>`; areas follow
   [`SPEC.md`](./SPEC.md#1-module-map).

2. **Per-tenant role grants** — in
   `identity/provision.py::provision_erp_tenant()` (W6).
   The function is called at tenant-onboard time and seeds
   a default set of roles for a new tenant. Add the new
   key to the `permissions` list of whichever system role
   should own it (e.g. `o2c.so.cancel` → `sales_manager`
   role). Existing tenants pick the new key up at their
   next role-edit (a small migration in W6); W6 ships the
   bulk-grant job.

> **Don't add a permission key in code without seeding the
> catalog row.** A `require_permission("erp.new.feature")` call
> that points at a key not in the catalog returns `False`
> silently. This is the desired fail-closed behaviour, but it
> is the wrong user experience for a new feature that should
> work out of the box. Catalog row first, then the gate.

## The merge-back flow

Waves are first reviewed in this repo, then mirrored to the
parent. The full lifecycle:

```
this repo (cautious-goggles-erp-v2)
└── main
    ├── feat/erp-v2-wN-<name>      ← work happens here
    │     │
    │     └── (PR → main, here)    ← squash-merge; CI green
    │
    └── (commit hash recorded as the wave's source)

parent repo (cautious-goggles)
└── feat/erp-crm                   ← integration branch
    │
    └── (PR titled "Wave N — <name>"; body = the parent-PR template
         in MERGE_PLAN.md; source commit hash from this repo's main)
```

Step by step:

1. Push your `feat/erp-v2-wN-*` branch here. Open a PR against
   `main` in this repo. CI runs unit + integration + coverage
   + the `erp-v2-check-coverage` guard.
2. Reviewers sign off. Squash-merge to `main` here. Note the
   resulting commit hash.
3. Open a PR against `cautious-goggles/feat/erp-crm`. Title
   follows `MERGE_PLAN.md` (e.g.
   `feat(erp-v2): O2C complete (multiline, credit, reservation, cancel, hold, invoice, payment, FIFO, tax, FX)`).
   Body uses the parent-PR template; the "Source branch" is
   this repo's `main` at the wave's commit hash.
4. Parent CI runs its own checks. The `feat/erp-crm` branch
   owner is the gate. After merge, Kong shift percentages
   may move (W6+).
5. W(N+1) work starts on a fresh `feat/erp-v2-w(N+1)-*` branch.

**No parent PR contains unreviewed code.** The wave's history
lives in this repo first.

## The coverage guard

`erp-v2-check-coverage` is the static-analysis CI guard that
every mutating route in a non-system path depends on
`require_permission` or `require_tenant_id`.

- **Where it lives:** `services/erp/scripts/check_coverage.py`.
- **How to run it locally:**

  ```bash
  cd services/erp
  erp-v2-check-coverage
  # or:
  erp-v2-check-coverage --strict
  ```

  Exit code is non-zero if any violation is found.

- **What it checks (W0):** every POST/PUT/PATCH/DELETE route
  registered in the FastAPI app has a `require_permission` or
  `require_tenant_id` dep.

- **What it does *not* check (yet):** that the perm key is in
  the catalog. A `require_permission("erp.bogus.key")` call
  passes the guard. The catalog check is a separate lint rule
  in W7.

- **System paths are exempt:** `/health`, `/ready`, `/metrics`,
  `/openapi.json`, `/docs`, `/redoc`. Adding a route to
  `_SYSTEM_PATHS` is the way to opt out — but routes added
  here should be idempotent and auth-free.

- **CI integration:** `.github/workflows/erp-v2-ci.yml` runs
  the guard on every PR. A failed guard blocks merge.

## Where the docs live

| Doc | Purpose |
|-----|---------|
| [`README.md`](../README.md) | Repo overview, why this repo exists, wave status |
| [`AUDIT.md`](../AUDIT.md) | The ~110 gaps from v1 (parent repo), each with source + severity |
| [`MERGE_PLAN.md`](../MERGE_PLAN.md) | Wave-by-wave cut plan into the parent |
| [`docs/SPEC.md`](./SPEC.md) | The v2 spec, wave by wave |
| [`docs/GETTING_STARTED.md`](./GETTING_STARTED.md) | 5-min quick start |
| [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md) | This file |
| [`docs/TROUBLESHOOTING.md`](./TROUBLESHOOTING.md) | Common failures and fixes |
| [`docs/waves/`](../docs/waves/) | One file per wave (W0 done; W1+ planned) |
| [`docs/adrs/`](../docs/adrs/) | The 10 ADRs that shape v2 |
| [`docs/trees/`](../docs/trees/) | Module tree (ERP, ERP-CRM) |
| [`docs/openapi/`](../docs/openapi/) | Generated `erp.json` (CI-gated) |
| [`docs/API_CHANGELOG.md`](./API_CHANGELOG.md) | Every additive / breaking HTTP change |

## Where to get help

- The `feat/erp-v2-w0-foundations` branch's PR thread is the
  most-active channel during W0.
- For v1 context, the parent repo's `cautious-goggles` has
  the v1 source under `services/erp/` and the v1 docs under
  `docs/architecture/`.
- The wave owner (per the parent-PR template) is the
  decision-maker for scope-creep questions.
