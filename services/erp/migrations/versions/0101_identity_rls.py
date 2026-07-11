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
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0101_identity_rls"
down_revision: str | Sequence[str] | None = "0100_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TENANT_TABLES = ("users", "roles", "role_permissions", "user_roles")
SENTINEL = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # All access goes through the app, which sets `app.tenant_id` GUC.
        # The COALESCE/NULLIF handles both unset and empty GUC gracefully
        # (the `::uuid` cast would otherwise fail on '').
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = COALESCE(
                    NULLIF(current_setting('app.tenant_id', true), ''),
                    '{SENTINEL}'
                )::uuid
            )
            WITH CHECK (
                tenant_id = COALESCE(
                    NULLIF(current_setting('app.tenant_id', true), ''),
                    '{SENTINEL}'
                )::uuid
            )
        """)


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
