"""W7 — CRM AI + notifications + custom fields + saved views + tickets UI.

W7 ships:
- `notifications` table + `NotificationService` (in-app feed, email/SMS
  adapters are stubbed).
- `ai_agent_runs` + `ai_recommendations` tables for the Sales Coach
  agent (suggest-only; LLM call is the W7.1 swap).
- `saved_views` for per-user list customisation.
- `custom_fields` for per-tenant schema extension.
- `ai_agent_memory` (pgvector-shaped) for RAG; W7 ships the table,
  W7.1 wires the actual embeddings.
- Tickets get a `GET /tickets/{id}` + `GET /tickets?status=...` for
  the support UI; tickets are created via the existing `POST /crm/tickets`
  is out of scope here — the API was W5. W7 adds the read API.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0110_w7_crm_ai"
down_revision: str | Sequence[str] | None = "0109_w6_platform"
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
    # ----- notifications -------------------------------------------- #
    op.create_table(
        "notifications",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("kind", sa.String(64), nullable=False),  # in_app | email | sms
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_notifications_unread", "notifications", ["user_id", "is_read", "created_at"])

    # ----- saved views --------------------------------------------- #
    op.create_table(
        "saved_views",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("filters", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sort", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_shared", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "name", "entity_type", name="uq_saved_views_user_name_entity"),
    )

    # ----- custom fields ------------------------------------------- #
    op.create_table(
        "custom_field_defs",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("field_type", sa.String(32), nullable=False),
        # text | number | date | boolean | select
        sa.Column("options", postgresql.JSONB, nullable=True),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "entity_type", "key", name="uq_custom_field_defs"),
    )

    op.create_table(
        "custom_field_values",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("custom_field_defs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("value_text", sa.Text, nullable=True),
        sa.Column("value_number", sa.Numeric(20, 4), nullable=True),
        sa.Column("value_date", sa.Date, nullable=True),
        sa.Column("value_bool", sa.Boolean, nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_custom_field_values_entity", "custom_field_values", ["field_id", "entity_id"])

    # ----- AI agent runs + recommendations ------------------------- #
    op.create_table(
        "ai_agent_runs",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("agent_key", sa.String(128), nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        # running | succeeded | failed
        sa.Column("input", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "ai_recommendations",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_agent_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("action_url", sa.String(512), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "ai_agent_feedback",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_recommendations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),  # up | down
        sa.Column("comment", sa.Text, nullable=True),
        *_timestamps(),
    )


def downgrade() -> None:
    tables = [
        "ai_agent_feedback",
        "ai_recommendations",
        "ai_agent_runs",
        "custom_field_values",
        "custom_field_defs",
        "saved_views",
        "notifications",
    ]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
