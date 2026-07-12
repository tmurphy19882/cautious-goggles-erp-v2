# v2 Audit — Wave-by-Wave (2026-07-11)

> **Scope:** Full code audit of the 10-wave ERP v2 build at HEAD of `main`.
> **Method:** AST parse, import dry-run, route inventory, RLS coverage
> check, target run of `pytest tests/unit/`, plus a few targeted tests
> against a real Postgres-less interpreter.

## Severity legend

- **P0 — Security / correctness** (data leaks, RLS gaps, auth bypass)
- **P1 — Won't run** (import error, missing file, missing fixture)
- **P2 — Wrong but recoverable** (typo in dep, misnamed permission)
- **P3 — Cosmetic / pre-existing** (test assertion wrong, dead code)

---

## Findings

### P0-1: W6 platform migration never enables RLS on its 7 tables
**File:** `services/erp/migrations/versions/0109_w6_platform.py`
**Impact:** Any tenant can read/write any other tenant's `tenants`,
`tenant_onboarding_log`, `webhook_subscriptions`, `webhook_deliveries`,
`payment_intents`, `feature_flags`, `audit_log`. The `_rls()` helper
is defined but never called. `downgrade()` references the tables
but the upgrades don't enable the policy.
**Fix:** Add `_rls(...)` calls after each `op.create_table` in the
upgrade, and ship a `0114_w6_w7_rls_repair` alembic that idempotently
adds RLS to the 11 affected tables (so existing deployments also
get covered).

### P1-1: `api/deps.py` doesn't exist
**Files (4 imports broken):**
- `src/crm/productivity_api.py:24` — `from api.deps import get_session, get_tenant_id, get_user_id`
- `src/hr/api.py:24`
- `src/ops/api.py:24`
- `src/trade/api.py:24`

**Impact:** The v2 app **fails to import** at startup. Every W7-W10
route is dead code. `create_app_v2()` raises `ModuleNotFoundError`
before serving a single request.
**Fix:** Create `src/api/deps.py` that re-exports the three names
from `identity.deps`, `shared.tenant`, and a new `get_user_id` helper
that reads `x-user-id` (parity with how `get_current_principal`
already does it).

### P1-2: 5 new packages missing `__init__.py`
**Files:**
- `src/crm/__init__.py` (missing)
- `src/hr/__init__.py` (missing)
- `src/platform/__init__.py` (missing)
- `src/trade/__init__.py` (missing)
- `src/ops/__init__.py` (missing)

**Impact:** Even if `api.deps` exists, `from crm.productivity import ...` still
fails because `crm` is not a package to Python. The bug surfaces
when the user site-path doesn't have a `crm` package collision.
**Fix:** Add an empty `__init__.py` to each.

### P1-3: W7-W10 tests use fixtures that don't exist
`test_w9_trade_polish.py`, `test_w10_ops_readiness.py`,
`test_w10_event_contracts.py` — all use `(session, tenant_id, user_id)`.

**Impact:** All 20 of these tests fail with "fixture not found"
on collection. The conftest exposes `pg_session_factory` and `client`
but not the lower-level trio the new tests need.
**Fix:** Add `session`, `tenant_id`, `user_id` fixtures to `conftest.py`
that derive from `pg_session_factory` + the seed tenant UUIDs
already declared at the top of the file.

### P2-1: W6 `POST /tenants` requires `x-tenant-id`
**File:** `src/platform/api.py:62`
**Symptom:** `dependencies=[Depends(require_permission("platform.tenant.write"))]`
chains into `get_current_principal` → `require_tenant_id` → raises
`TenantRequiredError` if the header is missing. The route comment
explicitly says "tenant onboarding is a SYSTEM operation; it does
not require x-tenant-id" — but the dep chain disagrees.
**Impact:** First-ever tenant can't be provisioned (chicken-and-egg).
**Fix:** Replace `require_permission(...)` with a service-account
allow-list check (system-level, e.g. `x-system-token` or
`Authorization: Bearer …` from the platform console).

### P2-2: W6 `POST /payments/intents` uses wrong permission key
**File:** `src/platform/api.py:155`
**Symptom:** `dependencies=[Depends(require_permission("platform.webhook.write"))]`
— copy-paste from the webhook route above. Should be
`platform.payment.write`.
**Impact:** Authorization is over-broad (a webhook-writer can also
create payment intents) and the right permission is never enforced.
**Fix:** Add `platform.payment.write` to the seeded permission list
in W0 and switch the dep key.

### P1-4: Empty/garbage directories from leftover stubs
**Files:** `src/ai/` (empty), `src/ai/rag/` (empty), `src/ai/registry/`
(empty), `src/legal/` (empty), `src/master-data/` (empty + duplicate
of `src/master_data/`).
**Impact:** Clutter, and `src/master-data/` (hyphenated) is a
non-Python directory name that is silently ignored by imports but
clutters the tree.
**Fix:** `mavis-trash` them.

### P3-1: W0 unit-test assertion wrong (Decimal padding)
**File:** `tests/unit/test_schemas.py:13`
**Symptom:** Asserts `Decimal("10.50")` is stored as `"10.5000"`.
Pydantic's `decimal_places=4` only *validates* the upper bound; it
doesn't pad. The model behaviour is correct; the assertion is wrong.
**Fix:** Change assertion to `assert m.value == Decimal("10.50")`
(or `Decimal("10.5000") == Decimal(m.value)` if the intent is
"≤4 places" not "exactly 4 places").

### P3-2: W0 unit-test assertion wrong (ErrorEnvelope details)
**File:** `tests/unit/test_errors.py:57`
**Symptom:** Asserts `model_dump(exclude_none=True)` omits the
`details` field. The field has `default_factory=dict`, so it's
serialised as `{}` (not `None`). `exclude_none=True` doesn't drop
empty containers.
**Fix:** Either change the field to `Optional[dict] = None`, or
update the test to use `exclude_unset=True` (which does omit
uninitialised fields).

---

## Summary

| Severity | Count |
|----------|-------|
| P0 (security / data leak) | 2 |
| P1 (won't run) | 4 |
| P2 (wrong but recoverable) | 2 |
| P3 (cosmetic / pre-existing) | 2 |

P0-1 and P0-2 are the headline findings — the entire W6 + W7
tenant-data surface is unprotected, which is a real RLS gap.
P1-1 + P1-2 are linked: even after fixing `api/deps.py`, the new
packages still need `__init__.py` to be importable.

The W0 findings (P3) predate the v2 rebuild and are recorded for
completeness; they should be fixed in a follow-up PR but are not
on the critical path of W6-W10.

## How this audit was run

1. `ast.parse` every `.py` under `src/` and `tests/` → all parse.
2. `py -c "from api.main import create_app_v2"` → `ModuleNotFoundError`
   on `crm.productivity_api` → `api.deps` missing (P1-1).
3. `py -m pytest tests/integration/test_w9_trade_polish.py --co -q`
   → `ModuleNotFoundError: No module named 'trade.service'`
   → `__init__.py` missing (P1-2).
4. Grepped W6-W10 migrations for `_rls(...)` calls in the
   `upgrade()` function body → W6 and W7 have **zero** (P0-1, P0-2);
   W8-W10 are clean.
5. `py -m pytest tests/unit/ -q --no-cov` → 31 passed, 2 failed (P3-1,
   P3-2), 3 errored (DB-required, expected without Postgres).
6. Read `src/platform/api.py` end-to-end → P2-1, P2-2.
7. Cross-referenced every `from api.deps import …` against the
   actual `api/` directory → P1-1.

## Environment caveat

The local Python install has `.pth` files that inject the
unrelated `C:\Users\trevo\Projects\manufacturing-erp\services\erp\src`
path into `sys.path`. This made `import trade` resolve to the
wrong package, masking the `__init__.py` issue behind a "wrong
trade" error. Resolved by reading the local v2 tree directly.
The fix is local; future audits should be aware of the path leak.
