# ADR-0007 — Soft delete on every domain table

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | W0 (decision + identity RLS migration sets the pattern); applies to every table from W1 onward |
| Supersedes | v1's hard `DELETE` paths |
| Related | [ADR-0010 — domain / infrastructure / workflows split](./0010-domain-infrastructure-split.md) |

## Context

v1's handlers issue `DELETE FROM ...` directly. Once a row is gone,
it's gone — no audit, no recovery, no "this was deleted by X on Y".
The v2 SPEC requires soft delete on every domain table for two
reasons:

1. **Audit + undo.** A support ticket that says "customer X was
   deleted in error, please restore" needs to be answerable from
   the data, not from a database backup.
2. **Cross-table references.** A `sales_orders` row points at
   `parties`, `products`, `warehouses`. Hard-deleting a party
   while there are open SOs pointing at it leaves dangling
   references (or, worse, requires `ON DELETE CASCADE` chains
   that destroy history).

Options:

- **Hard delete + audit log.** Append-only `audit_log` table
  captures the pre-delete row. Works for audit; doesn't help
  with cross-table references (`SELECT` from `parties` returns
  nothing).
- **Hard delete + `deleted_at` mirror in audit table.** Same
  cost, same gap.
- **Soft delete: `deleted_at TIMESTAMPTZ NULL` on the row.** The
  row stays; queries filter `WHERE deleted_at IS NULL`; a
  scheduled job hard-deletes rows older than the retention
  window (default 7 years; configurable per tenant).
- **`is_deleted BOOLEAN` instead of timestamp.** Loses the
  "when" of deletion. The timestamp is the cheaper option that
  keeps the "when."

## Decision

Every domain table in v2 has a `deleted_at TIMESTAMPTZ NULL`
column.

- **All `SELECT` queries include `.where(Model.deleted_at.is_(None))`**
  in service code. The W0 identity queries set the pattern; the
  W0 RLS migration includes `deleted_at` in the policy's
  `USING` clause so the RLS check is at the database boundary
  and is not bypassed by an ORM-level `WHERE` omission.
- **All `DELETE` calls become "soft delete"** — a PATCH
  `{"deleted_at": "<now>"}`. The hard-delete is a separate
  scheduled job (`purge_deleted_rows`) that runs weekly and
  removes rows where `deleted_at < now() - retention_window`.
- **Restoration** is a PATCH `{"deleted_at": null}`. UI is W6.
- **Cascade behaviour.** A row that is soft-deleted stays
  referenceable. A hard-delete of a still-referenced row
  requires explicit unbinding; the cascade is the scheduled
  purge job, not the DB FK.
- **`DeletedFilter` middleware / query helper** is a thin
  wrapper that adds `.where(deleted_at.is_(None))` to every
  list endpoint. It does not bypass an explicit
  `include_deleted=True` flag (admin-only, RBAC-gated).

## Consequences

### Positive

- **No accidental data loss.** A misclicked delete is reversible.
- **Audit is built in.** "When was this row deleted, by whom,
  from what IP" is a query on the row itself plus the
  `audit_log` table.
- **Cross-table references are stable.** A soft-deleted party
  can still be the `party_id` on a soft-deleted SO; the
  historical query path is intact.
- **Retention is configurable per tenant.** A tenant that needs
  to keep soft-deleted rows for 10 years (regulated industry)
  can; a tenant that wants 30 days can.

### Negative / costs

- **Every query has to remember the filter.** A list endpoint
  that forgets `.where(deleted_at.is_(None))` returns
  "deleted" rows to the user. Mitigations: the
  `DeletedFilter` helper, code review, and the lint rule
  (W7) that flags list queries against a model with
  `deleted_at` and no `.where(deleted_at...)`.
- **Indexing.** `deleted_at` is sparse; an index on
  `(tenant_id, deleted_at, created_at)` covers the common
  "list active rows, newest first" path. W1's index strategy
  is part of every migration that ships a new table.
- **Storage grows.** Soft-deleted rows consume disk until the
  purge job runs. Acceptable cost; monitored by the
  `soft_deleted_rows_total` counter (W7).
- **Restoration UI is W6.** Until then, restoration is a SQL
  UPDATE by the support team with RBAC override.

### What we lose vs v1

- The "fast path" of `DELETE FROM parties WHERE id = ?`. The
  replacement (`UPDATE parties SET deleted_at = now() WHERE
  id = ?`) is a single statement and is no slower in
  practice (same row touch, same WAL write).
- Implicit `ON DELETE CASCADE` chains. v2 keeps the FKs but
  doesn't rely on cascade; the cascade is the purge job.

### W0 scope

W0 ships:

- The `deleted_at` column on `users`, `roles`,
  `role_permissions`, `user_roles` (migration `0100_identity`).
- The pattern: every W0 service-layer query includes
  `.where(deleted_at.is_(None))`.

W0 does **not** ship:

- `DeletedFilter` middleware (W1).
- `purge_deleted_rows` scheduled job (W1).
- Restoration UI (W6).
