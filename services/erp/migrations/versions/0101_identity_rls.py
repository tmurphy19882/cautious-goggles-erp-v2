"""identity RLS — FORCE row level security on tenant-scoped identity tables

Revision ID: 0101_identity_rls
Revises: 0100_identity
Create Date: 2026-07-11

Wave 0 — Foundations.

`permissions` is intentionally NOT tenant-scoped (it's a global catalog
keyed by `key`), so it does not get RLS. Everything else in the identity
schema is tenant-scoped and FORCE RLS.

Closes BUG-003: the RLS policy used to do
    tenant_id = current_setting('app.tenant_id', true)::uuid
which raised `invalid input syntax for type uuid` when the GUC was set
to '' (the legacy `set_tenant_context(None)` behaviour). The new policy
COALESCEs the GUC to a sentinel UUID (which never matches a real
tenant) so system paths get zero rows instead of an error.

Join tables (`role_permissions`, `user_roles`) don't have a `tenant_id`
column — they inherit the tenant from the parent (`roles`, `users`).
The policy is an `EXISTS` subquery against the parent so the join is
always tenant-scoped without denormalising the column.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0101_identity_rls"
down_revision: str | Sequence[str] | None = "0100_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables with a `tenant_id` column.
_TENANT_TABLES = ("users", "roles")
# Join tables — RLS uses EXISTS against the parent table.
_JOIN_TABLES = (("role_permissions", "role_id", "roles"),
                ("user_roles", "user_id", "users"))

SENTINEL = "00000000-0000-0000-0000-000000000000"


def _tenant_predicate() -> str:
    """SQL fragment for the current tenant id with the BUG-003
    sentinel fallthrough. Use as `tenant_id = <fragment>` in a
    policy."""
    return (
        "COALESCE("
        "NULLIF(current_setting('app.tenant_id', true), ''),"
        f"'{SENTINEL}'"
        ")::uuid"
    )


def upgrade() -> None:
    pred = _tenant_predicate()
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = {pred})
            WITH CHECK (tenant_id = {pred})
        """)

    for table, parent_col, parent_table in _JOIN_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (EXISTS (
                SELECT 1 FROM {parent_table}
                WHERE {parent_table}.id = {table}.{parent_col}
                AND {parent_table}.tenant_id = {pred}
            ))
            WITH CHECK (EXISTS (
                SELECT 1 FROM {parent_table}
                WHERE {parent_table}.id = {table}.{parent_col}
                AND {parent_table}.tenant_id = {pred}
            ))
        """)


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table, _parent_col, _parent_table in _JOIN_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
