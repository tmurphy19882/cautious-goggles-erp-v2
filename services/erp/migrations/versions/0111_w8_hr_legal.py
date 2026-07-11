"""W8 — HR (employees, departments, contracts) + Legal (contracts, e-sign).

W8 ships the people-and-paperwork surface. W8.1 wires the actual
e-sign provider (DocuSign-shaped); W8 ships the schema and a
deterministic state machine.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0111_w8_hr_legal"
down_revision: str | Sequence[str] | None = "0110_w7_crm_ai"
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
    # ----- HR: departments ------------------------------------------- #
    op.create_table(
        "departments",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("manager_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
    )
    _rls("departments")

    # ----- HR: employees --------------------------------------------- #
    op.create_table(
        "employees",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        # link to a portal user if the employee has a login
        sa.Column("employee_number", sa.String(32), nullable=False),
        sa.Column("first_name", sa.String(128), nullable=False),
        sa.Column("last_name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("title", sa.String(128), nullable=True),
        sa.Column("hire_date", sa.Date, nullable=True),
        sa.Column("termination_date", sa.Date, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        # active | on_leave | terminated
        sa.Column("manager_employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "employee_number", name="uq_employees_tenant_number"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_employees_tenant_email"),
    )
    _rls("employees")

    # ----- HR: payroll exports --------------------------------------- #
    op.create_table(
        "payroll_exports",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        # pending | generated | sent | failed
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("file_url", sa.String(512), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        *_timestamps(),
    )
    _rls("payroll_exports")

    # ----- Legal: contract templates --------------------------------- #
    op.create_table(
        "contract_templates",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # nda | msa | sow | employment
        sa.Column("body_md", sa.Text, nullable=False),
        # markdown with {{handlebar}} placeholders
        sa.Column("variables", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "name", "kind", name="uq_contract_templates_tenant_name_kind"),
    )
    _rls("contract_templates")

    # ----- Legal: contracts ------------------------------------------ #
    op.create_table(
        "contracts",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contract_templates.id"), nullable=False, index=True),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True, index=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        # draft | sent | signed | countersigned | active | expired | terminated
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("expiration_date", sa.Date, nullable=True),
        sa.Column("rendered_body_md", sa.Text, nullable=False),
        sa.Column("variables", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
    )
    _rls("contracts")

    # ----- Legal: e-sign envelopes ----------------------------------- #
    op.create_table(
        "esign_envelopes",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="docusign"),
        sa.Column("provider_envelope_id", sa.String(128), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        # created | sent | delivered | completed | declined | voided
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    _rls("esign_envelopes")

    # ----- Legal: signature events ----------------------------------- #
    op.create_table(
        "esign_events",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("envelope_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("esign_envelopes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        # sent | viewed | signed | declined | completed
        sa.Column("signer_email", sa.String(255), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    _rls("esign_events")


def downgrade() -> None:
    tables = [
        "esign_events",
        "esign_envelopes",
        "contracts",
        "contract_templates",
        "payroll_exports",
        "employees",
        "departments",
    ]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
