# ADR-0005 — RBAC on every mutator

| Field | Value |
|-------|-------|
| Status | **Accepted** (W0 ships the table + service; W1 wires real JWT) |
| Date | 2026-07-11 |
| Wave | W0 (table + service + read API + dep factory); W1 (real JWT) |
| Supersedes | v1's lack of RBAC (audit items RBAC-1, RBAC-2, RBAC-5) |
| Related | [ADR-0001 — wave-based delivery](./0001-wave-based-delivery.md) |

## Context

v1 has no role-based access control. Every authenticated user can
read and write every row in `services/erp`. The frontend
implements "user A can't see tenant B's data" by hiding the UI,
not by rejecting server-side. This is a P0 audit item.

What v2 needs:

- A **global permission catalog** — every action a principal can
  attempt, with a stable dotted key (`erp.so.create`,
  `erp.invoice.write`, `crm.ticket.close`, …).
- A **tenant-scoped role** concept — a role belongs to one tenant,
  can hold any subset of the global catalog, and can be assigned
  to one or more users in that tenant.
- A **service** that takes a principal and a permission key and
  returns yes/no (or raises).
- A **dep factory** so every mutating route can write
  `dependencies=[Depends(require_permission("erp.so.write"))]`
  and get the right behaviour.
- A **static guard** that fails the build if a mutating route
  in a non-system path is *not* gated by `require_permission` or
  `require_tenant_id`. This is `erp-v2-check-coverage`.

Options:

- **Casbin / OPA / bespoke policy engine.** Powerful, but
  overkill for the W0 scope; the policy syntax becomes a second
  language to learn. We can adopt OPA later if the rules grow
  beyond what the dep factory can express.
- **Django / FastAPI-User's permission classes.** Library-bound;
  not aligned with our async-SQLAlchemy + Pydantic v2 stack.
- **Custom `PermissionService` + per-tenant roles in a join
  table.** Plain SQL, no library, easy to reason about. This is
  what we ship.

## Decision

- **Schema.** `users`, `roles`, `permissions`, `role_permissions`,
  `user_roles`. All tenant-scoped *except* `permissions`, which
  is the global catalog. Defined in migration
  `migrations/versions/0100_identity.py` (W0).
- **Permission keys** are dotted strings: `<area>.<resource>.<action>`.
  Seeded in `0100_identity` (~50+ entries spanning every module
  in [`SPEC.md`](../SPEC.md#1-module-map)).
- **Roles are tenant-scoped.** Two tenants can each have a role
  called "Sales Manager" with different permission grants.
  Per-tenant role seeding is the W6 `provision_erp_tenant()` job.
- **`PermissionService.assert_can(principal, key)`** is the single
  chokepoint. It hits the DB once per request, caches the
  principal's effective permission set on the `Principal`
  dataclass, and raises `PermissionDenied` (which the global
  error handler turns into a `403` envelope).
- **Dep factory `require_permission(key)`** — returns a FastAPI
  dep. Use as
  `dependencies=[Depends(require_permission("erp.so.write"))]`.
- **Static guard `erp-v2-check-coverage`** (W0, script in
  `services/erp/scripts/check_coverage.py`) — walks the FastAPI
  app at import time and exits non-zero if any mutator in a
  non-system path is missing the dep.

## Consequences

### Positive

- **One chokepoint.** Every authorisation decision goes through
  `PermissionService.assert_can`. No decorator chains, no
  per-router policies, no scattered `if user.can(...)` checks.
- **The catalog is the contract.** Adding a new permission is a
  one-line INSERT in `0100_identity` (or a follow-up migration).
  Removing one is a follow-up migration that drops from the
  catalog and the seeded role grants.
- **The static guard is automatic.** A PR that adds a new
  mutating route without `require_permission` fails CI before
  review.
- **Per-tenant roles** are the only RBAC unit the platform
  exposes. Frontend's role/permission matrix (W6) reads from
  the same tables.

### Negative / costs

- **Performance: one DB hit per request** to load the
  principal's effective permission set. Mitigated by caching on
  the `Principal` dataclass (loaded once in
  `get_current_principal`); further mitigation in W7 with a
  per-request LRU.
- **Catalog drift.** New modules need new permission keys; a
  module that ships without catalog entries can't be gated.
  The wave template requires the new keys to be added in the
  same PR as the new router.
- **W0's auth is a header, not a JWT.** `get_current_principal`
  reads `x-user-id` and trusts it. This is intentional for W0
  (so the dep is exercisable end-to-end without a JWT issuer)
  and is replaced in W1 with a real JWT validator against
  Keycloak JWKS. W0's auth is **not** a security boundary; it
  is a development affordance.

### W0 scope (concrete)

| Shipped | Not shipped (deferred) |
|---------|-------------------------|
| `users`, `roles`, `permissions`, `role_permissions`, `user_roles` tables | Real JWT validation (W1) |
| `PermissionService.{load_principal,can,assert_can,grant_to_role}` | Per-tenant role seeding on tenant-onboard (W6) |
| `require_permission(key)` dep factory | RBAC admin UI in `/settings/roles` (W6) |
| `GET /api/v1/erp/identity/permissions` (global catalog) | RBAC write API (W6) |
| `GET /api/v1/erp/identity/roles` (per-tenant, gated) | Audit log read API (W6) |
| `GET /api/v1/erp/identity/roles/{role_id}` | — |
| `erp-v2-check-coverage` static guard | — |
| W0 principal: `x-user-id` header | W1: JWT (`sub` + `tenant_id` + `scope`) |

### What we lose vs v1

- Nothing. v1 had no RBAC.
