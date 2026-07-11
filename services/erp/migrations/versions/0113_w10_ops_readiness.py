"""W10 — Operational readiness.

- `outbox_dlq` table for poison messages.
- `event_schema_versions` table for the Apicurio contract gate.
- `api_request_metrics` for per-tenant + per-route observability.
- Service helpers for DLQ + contract gate + metrics recording.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0113_w10_ops_readiness"
down_revision: str | Sequence[str] | None = "0112_w9_trade_polish"
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
    ]


def upgrade() -> None:
    # ----- outbox DLQ ------------------------------------------------ #
    op.create_table(
        "outbox_dlq",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("original_outbox_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("event_type", sa.String(128), nullable=False, index=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_outbox_dlq_unresolved", "outbox_dlq", ["tenant_id", "resolved_at"])
    _rls("outbox_dlq")

    # ----- event schema versions (Apicurio contract gate) ----------- #
    op.create_table(
        "event_schema_versions",
        _uuid_pk(),
        # global table — contract gate is per event_type, not per tenant
        sa.Column("event_type", sa.String(128), primary_key=True, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("schema_json", postgresql.JSONB, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ----- per-tenant API request metrics --------------------------- #
    op.create_table(
        "api_request_metrics",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("route", sa.String(255), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_api_request_metrics_tenant_time", "api_request_metrics", ["tenant_id", "ts"])
    op.create_index("ix_api_request_metrics_route", "api_request_metrics", ["route", "ts"])
    _rls("api_request_metrics")


def downgrade() -> None:
    _drop_rls("api_request_metrics")
    op.drop_table("api_request_metrics")
    op.drop_table("event_schema_versions")
    _drop_rls("outbox_dlq")
    op.drop_table("outbox_dlq")
