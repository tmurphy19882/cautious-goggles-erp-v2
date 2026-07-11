"""W5 — CRM core (leads, opportunities, pipeline, quotes, activities, tickets).

Closes the W5 punch list from the audit:

- CRM-1: lead capture
- CRM-2: lead qualification + conversion to opportunity
- CRM-3: pipeline + kanban
- CRM-4: opportunity stage transitions
- CRM-5: quote creation + convert-to-SO (the long-promised
  `POST /quotes/{id}/convert-to-so` — O2C-15)
- CRM-6: activity timeline on the Party 360°
- CRM-7: support ticket create + comment + status
- BUG-021: `/identity/permissions` was world-readable; the W0
  hotfix already closed that.

All tables are tenant-scoped with FORCE RLS.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0108_w5_crm"
down_revision: str | Sequence[str] | None = "0107_w3_master_data"
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
    # ----- pipelines + stages ---------------------------------------- #
    op.create_table(
        "pipelines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "name", name="uq_pipelines_tenant_name"),
    )
    _rls("pipelines")

    op.create_table(
        "pipeline_stages",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("probability_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("is_won", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_lost", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.UniqueConstraint("pipeline_id", "key", name="uq_pipeline_stages_key"),
    )
    _rls("pipeline_stages")

    # ----- leads ----------------------------------------------------- #
    op.create_table(
        "leads",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        # new | qualified | unqualified | converted
        sa.Column("score", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("converted_party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=True),
        sa.Column("converted_opportunity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_leads_status", "leads", ["tenant_id", "status"])
    _rls("leads")

    # ----- opportunities --------------------------------------------- #
    op.create_table(
        "opportunities",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=False, index=True),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=False, index=True),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_stages.id"), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("probability_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("expected_close_date", sa.Date, nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        # open | won | lost | abandoned
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(255), nullable=True),
        sa.Column("converted_sales_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_orders.id"), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_opportunities_stage", "opportunities", ["tenant_id", "stage_id"])
    _rls("opportunities")

    # ----- quotes ---------------------------------------------------- #
    op.create_table(
        "quotes",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("quote_number", sa.String(32), nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("opportunities.id"), nullable=True, index=True),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parties.id"), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        # draft | sent | accepted | rejected | expired | converted
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("subtotal", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_sales_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_orders.id"), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "quote_number", name="uq_quotes_tenant_number"),
    )
    _rls("quotes")

    op.create_table(
        "quote_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default="each"),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tax_rules.id"), nullable=True),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("line_total", sa.Numeric(20, 4), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("quote_id", "line_number", name="uq_quote_lines_quote_line"),
    )
    _rls("quote_lines")

    # ----- activities (timeline on Party 360°) ----------------------- #
    op.create_table(
        "activities",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("subject_type", sa.String(64), nullable=False),  # party | lead | opportunity | quote | so | po
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False),  # call | email | meeting | note | task
        sa.Column("summary", sa.String(255), nullable=False),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("duration_minutes", sa.Integer, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_activities_subject", "activities", ["subject_type", "subject_id", "occurred_at"])
    _rls("activities")

    # ----- tickets --------------------------------------------------- #
    op.create_table(
        "tickets",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("ticket_number", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),  # party | so | po | product
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("summary", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        # open | in_progress | waiting_customer | resolved | closed
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        # low | normal | high | urgent
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reporter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "ticket_number", name="uq_tickets_tenant_number"),
    )
    op.create_index("ix_tickets_status", "tickets", ["tenant_id", "status"])
    _rls("tickets")

    op.create_table(
        "ticket_comments",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_internal", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
    )
    _rls("ticket_comments")


def downgrade() -> None:
    tables = [
        "ticket_comments",
        "tickets",
        "activities",
        "quote_lines",
        "quotes",
        "opportunities",
        "leads",
        "pipeline_stages",
        "pipelines",
    ]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
