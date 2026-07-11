"""idempotency_keys — Idempotency-Key store for mutating routes

Revision ID: 0102_idempotency_keys
Revises: 0101_identity_rls
Create Date: 2026-07-11

Wave 0 — Foundations.

Bug fixes in this migration:

- **BUG-001**: every mutating request crashes on a fresh DB because
  the `idempotency_keys` table is referenced by `shared/idempotency.py`
  but no migration creates it. This migration creates it.
- **BUG-002**: column is `UUID` (not `String`) so it can be RLS-joined
  to the rest of the tenant-scoped schema. Composite PK on
  `(tenant_id, key_hash)` so two tenants can use the same key without
  colliding.
- **ADOPT_NOW item 1**: `key_hash` is `sha256(f"{tenant_id}:{key}")`
  (tenant-prefixed) so the same key from different tenants is hashed
  into different buckets. The middleware in `shared/idempotency.py`
  computes the hash this way too.

RLS: enabled + forced with the same pattern as 0101. The policy falls
through to a sentinel UUID when `app.tenant_id` is unset so a missing
GUC returns zero rows instead of erroring on a `::uuid` cast (see
0101 for the full rationale).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0102_idempotency_keys"
down_revision: str | Sequence[str] | None = "0101_identity_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Sentinel for "no tenant set" — see 0101 for the policy cast pattern.
_NO_TENANT_SENTINEL = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_body", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])

    # RLS — same pattern as 0101; sentinel fallthrough so unset GUC
    # returns zero rows instead of erroring on `::uuid` cast.
    op.execute("ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY idempotency_keys_tenant_isolation ON idempotency_keys
        USING (
          tenant_id = COALESCE(
            NULLIF(current_setting('app.tenant_id', true), ''),
            '{_NO_TENANT_SENTINEL}'
          )::uuid
        )
        WITH CHECK (
          tenant_id = COALESCE(
            NULLIF(current_setting('app.tenant_id', true), ''),
            '{_NO_TENANT_SENTINEL}'
          )::uuid
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS idempotency_keys_tenant_isolation ON idempotency_keys")
    op.execute("ALTER TABLE idempotency_keys NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_keys DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
