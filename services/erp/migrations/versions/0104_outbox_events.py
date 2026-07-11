"""outbox_events — transactional outbox for v2 events.

Revision ID: 0104_outbox_events
Revises: 0103_seed_tenant
Create Date: 2026-07-11

Closes the missing outbox table (W0 had the model but no migration).
Schema is identical to v1's `services/erp/src/models.py:OutboxEvent`
per ADR-0011.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0104_outbox_events"
down_revision: str | Sequence[str] | None = "0103_seed_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("topic", sa.String(256), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    # The poller's "unpublished, oldest first" query.
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["published_at", "created_at"],
    )

    # RLS so outbox rows are tenant-scoped (the poller filters by tenant
    # already, but defense in depth).
    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY")
    SENTINEL = "00000000-0000-0000-0000-000000000000"
    op.execute(f"""
        CREATE POLICY outbox_events_tenant_isolation ON outbox_events
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
    op.execute("DROP POLICY IF EXISTS outbox_events_tenant_isolation ON outbox_events")
    op.execute("ALTER TABLE outbox_events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbox_events DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_outbox_events_unpublished", table_name="outbox_events")
    op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
    op.drop_table("outbox_events")
