"""W9 — Trade compliance polish.

W9 ships:
- `hts_codes` (deterministic prefix tree) + HTS auto-resolver from
  product description keywords.
- FTZ (Foreign Trade Zone) entries: admission, removal, weekly
  inventory. Closes TR-3.
- Re-screening on party update against OFAC SDN (W9 ships a stub
  list + the screening pipeline; W9.1 swaps in the real feed).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0112_w9_trade_polish"
down_revision: str | Sequence[str] | None = "0111_w8_hr_legal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SENTINEL = "00000000-0000-0000-0000-000000000000"


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("uuid_generate_v4()"),
    )


def _tenant_uuid() -> sa.Column:
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
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


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    # ----- HTS codes (tenant-scoped cache of the public HTS schedule) - #
    op.create_table(
        "hts_codes",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(16), nullable=False),
        # 10-digit HTS code
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.String), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("duty_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("unit", sa.String(8), nullable=True),  # kg, m2, no, …
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_hts_codes_tenant_code"),
    )
    op.create_index("ix_hts_codes_keywords", "hts_codes", ["keywords"], postgresql_using="gin")
    _rls("hts_codes")

    # ----- FTZ entries ----------------------------------------------- #
    op.create_table(
        "ftz_entries",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("entry_number", sa.String(32), nullable=False),
        sa.Column("zone_id", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="admitted"),
        # admitted | removed | weekly_inventory | exported | destroyed
        sa.Column("admission_date", sa.Date, nullable=False),
        sa.Column("removal_date", sa.Date, nullable=True),
        sa.Column("hts_code", sa.String(16), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(8), nullable=True),
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "entry_number", name="uq_ftz_entries_tenant_entry"),
    )
    _rls("ftz_entries")

    # ----- Denied-party screening snapshots -------------------------- #
    op.create_table(
        "screening_snapshots",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False),  # ofac_sdn | bis_denied | eu_consolidated
        sa.Column("matched", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("match_score", sa.Numeric(4, 2), nullable=True),  # 0.00 - 1.00
        sa.Column("matched_entry", postgresql.JSONB, nullable=True),
        sa.Column("screened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_screening_snapshots_party_time",
        "screening_snapshots",
        ["party_id", "screened_at"],
    )
    _rls("screening_snapshots")


def downgrade() -> None:
    tables = ["screening_snapshots", "ftz_entries", "hts_codes"]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
