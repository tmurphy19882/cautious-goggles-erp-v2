# ADR-0012 — Idempotency key hash includes the tenant id

- **Status:** Accepted
- **Date:** 2026-07-11
- **Decider:** Mavis (owner) + the `w0-deep-research` worker
- **Source:** `docs/RESEARCH_V1_PATTERNS.md` § Idempotency + `ADOPT_NOW.md` § 1

## Context

v1's `shared_kernel.idempotency.store.hash_idempotency_key(key, *, tenant_id)`
computes `sha256(f"{tenant_id}:{key}".encode("utf-8"))`. v2 W0's
`shared/idempotency.py:_hash_idempotency_key` (originally) computed
`sha256(key.encode("utf-8"))` with no tenant prefix.

The v2 W0 schema uses a composite primary key `(tenant_id, key_hash)`,
which prevents cross-tenant row collision in the table. So the missing
tenant prefix is not a *runtime* bug — but it is a *defense-in-depth*
gap: two tenants who pick the same `Idempotency-Key` value
(`"order-2026-07-11-001"` is a common default for retrying clients)
end up with the same `key_hash`. A future refactor that drops the
`tenant_id` column from the PK (e.g. to enable per-tenant-key caches)
would silently introduce a real cross-tenant replay vector.

## Decision

v2's `_hash_idempotency_key` matches v1's byte-for-byte:
`sha256(f"{tenant_id}:{key}".encode("utf-8")).hexdigest()`.

The migration in `0102_idempotency_keys.py` and the
`DbIdempotencyStore` (in `shared/idempotency.py`) are both written
against this contract. The unit test
`tests/unit/test_idempotency.py::test_hash_differs_across_tenants_for_same_key`
is the regression guard.

## Consequences

- Two tenants using the same `Idempotency-Key` get different `key_hash`
  values; the `idempotency_keys` table's `key_hash` column is
  globally unique (even with the current composite PK).
- A future migration that relaxes the composite PK to a single-column
  PK on `key_hash` is safe.
- W1+ can adopt v1's `shared-kernel` package wholesale (or vice
  versa) without a hash-collision migration.
- A test that runs both v1's `hash_idempotency_key` and v2's
  `_hash_idempotency_key` against the same `(tenant, key)` produces
  the same hex — verified by
  `test_idempotency.py::test_hash_matches_sha256_of_tenant_key_concat`.
