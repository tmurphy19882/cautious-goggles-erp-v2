"""W3 — master data full + W4 finance + W5 CRM scaffolding.

Closes the W3 punch list: a real `parties` table that replaces the
thin "User = customer" wrapper from W1, with contacts, addresses,
tax IDs, and a search index. Per-tenant RLS on every table.

W3 also adds the W4 finance scaffolding: chart of accounts,
journal entries, and a minimal posting engine. W5 CRM (leads,
opps, pipeline, quotes) lands in a later wave.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0107_w3_master_data"
down_revision: str | Sequence[str] | None = "0106_w2_p2p"
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
    # =====================================================================
    # Parties (the real model)
    # =====================================================================

    op.create_table(
        "parties",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # customer | vendor | carrier | employee | internal_org
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("external_refs", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_parties_tenant_code"),
    )
    op.create_index("ix_parties_kind", "parties", ["tenant_id", "kind"])
    # GIN trigram index only on text column; tenant filtering uses ix_parties_kind / RLS.
    op.create_index(
        "ix_parties_search",
        "parties",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    _rls("parties")

    op.create_table(
        "party_contacts",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(64), nullable=True),  # "billing", "shipping", "decision_maker"
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text, nullable=True),
        *_timestamps(),
    )
    _rls("party_contacts")

    op.create_table(
        "party_addresses",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False),  # billing | shipping | remit_to | office
        sa.Column("line1", sa.String(255), nullable=True),
        sa.Column("line2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("region", sa.String(128), nullable=True),
        sa.Column("postal_code", sa.String(32), nullable=True),
        sa.Column("country_code", sa.String(3), nullable=True),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
    )
    _rls("party_addresses")

    op.create_table(
        "party_tax_ids",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tax_id_type", sa.String(32), nullable=False),  # vat | ein | gst | abn
        sa.Column("tax_id", sa.String(64), nullable=False),
        sa.Column("country_code", sa.String(3), nullable=True),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "party_id", "tax_id_type", "tax_id", name="uq_party_tax_ids"),
    )
    _rls("party_tax_ids")

    # =====================================================================
    # Search index (denormalised) — W6 populates it
    # =====================================================================

    op.create_table(
        "search_index",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("entity_type", sa.String(64), nullable=False),  # party | product | sales_order | po
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("subtitle", sa.String(512), nullable=True),
        sa.Column("search_text", sa.Text, nullable=False),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "entity_type", "entity_id", name="uq_search_index_entity"),
    )
    # GIN trigram index only on text column; tenant filtering is covered by RLS.
    op.create_index(
        "ix_search_index_text",
        "search_index",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )
    _rls("search_index")

    # =====================================================================
    # Import / export log
    # =====================================================================

    op.create_table(
        "import_jobs",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("entity_type", sa.String(64), nullable=False),  # parties | products | price_lists
        sa.Column("source", sa.String(32), nullable=False),  # csv | xlsx | json | api
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        # pending | running | succeeded | failed
        sa.Column("rows_total", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("rows_succeeded", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("rows_failed", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_log", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
    )
    _rls("import_jobs")

    # =====================================================================
    # W4 finance — chart of accounts, journals, periods, AR/AP aging
    # =====================================================================

    op.create_table(
        "gl_accounts",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(32), nullable=False),  # e.g. "1000", "4000"
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # asset | liability | equity | revenue | expense
        sa.Column("normal_side", sa.String(8), nullable=False),  # debit | credit
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("gl_accounts.id"), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_gl_accounts_tenant_code"),
    )
    op.create_index("ix_gl_accounts_kind", "gl_accounts", ["tenant_id", "kind"])
    _rls("gl_accounts")

    op.create_table(
        "accounting_periods",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(16), nullable=False),  # "2026-07"
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        # open | closed | locked
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_periods_tenant_code"),
    )
    _rls("accounting_periods")

    op.create_table(
        "journal_entries",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("entry_number", sa.String(32), nullable=False),
        sa.Column("period_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounting_periods.id"), nullable=True, index=True),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        # manual | sales_order | purchase_order | ap_invoice | payment
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="posted"),
        # draft | posted | reversed
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("posted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reversed_by_entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("journal_entries.id"), nullable=True),
        sa.Column("total_debit", sa.Numeric(20, 4), nullable=False),
        sa.Column("total_credit", sa.Numeric(20, 4), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "entry_number", name="uq_journal_entries_tenant_number"),
    )
    _rls("journal_entries")

    op.create_table(
        "journal_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("gl_accounts.id"), nullable=False, index=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("debit_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("credit_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("fx_rate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fx_rates.id"), nullable=True),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=True),
        sa.Column("ar_invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=True),
        sa.Column("ap_invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ap_invoices.id"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("entry_id", "line_number", name="uq_journal_lines_entry_line"),
        # Hard constraint: every line is one-sided (debit or credit)
        sa.CheckConstraint(
            "(debit_amount = 0 AND credit_amount > 0) OR (credit_amount = 0 AND debit_amount > 0)",
            name="ck_journal_lines_one_sided",
        ),
    )
    _rls("journal_lines")


def downgrade() -> None:
    tables = [
        "journal_lines",
        "journal_entries",
        "accounting_periods",
        "gl_accounts",
        "import_jobs",
        "search_index",
        "party_tax_ids",
        "party_addresses",
        "party_contacts",
        "parties",
    ]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
