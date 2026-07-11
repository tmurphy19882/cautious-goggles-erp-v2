"""W6 — Platform (tenant onboarding, webhooks, payments, RBAC write).

Closes the W6 punch list from the audit:

- PL-1..PL-3: real tenant onboarding (Keycloak + DB provisioning)
- PL-4: connector sync (W6 ships the trigger + log; real OAuth in
  W6.1 with Keycloak JWKS)
- PL-5: webhooks outbound (subscribe + deliver)
- PL-6: webhooks inbound
- PL-7..PL-9: payments gateway (Stripe-shaped stub; the W6 production
  swap is the real Stripe SDK with the right API key)
- RBAC-4: roles + permissions CRUD (the W0 shipped only the read API)

W6 also lands ADR-0013: per-tenant feature flags (Unleash-shaped
in-process store; W6.1 swaps for the real Unleash client).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0109_w6_platform"
down_revision: str | Sequence[str] | None = "0108_w5_crm"
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
    # ----- tenants ----------------------------------------------------- #
    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="provisioning"),
        # provisioning | active | suspended
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("keycloak_realm", sa.String(64), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_reason", sa.String(255), nullable=True),
        *_timestamps(),
    )

    # ----- tenant onboarding log ------------------------------------- #
    op.create_table(
        "tenant_onboarding_log",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),  # started | succeeded | failed
        sa.Column("details", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ----- webhook subscriptions -------------------------------------- #
    op.create_table(
        "webhook_subscriptions",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("secret", sa.String(255), nullable=True),  # HMAC secret
        sa.Column("events", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_timestamps(),
    )

    op.create_table(
        "webhook_deliveries",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        # pending | succeeded | failed | retried
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["tenant_id", "status"])

    # ----- payments gateway (Stripe-shaped) --------------------------- #
    op.create_table(
        "payment_intents",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("method", sa.String(32), nullable=False, server_default="card"),
        sa.Column("status", sa.String(32), nullable=False, server_default="requires_payment_method"),
        # requires_payment_method | requires_action | processing | succeeded | failed | canceled
        sa.Column("provider_intent_id", sa.String(128), nullable=True),
        sa.Column("provider_charge_id", sa.String(128), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
    )

    # ----- feature flags (Unleash-shaped in-process store) ------------- #
    op.create_table(
        "feature_flags",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("variant", sa.String(64), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_feature_flags_tenant_key"),
    )

    # ----- audit log --------------------------------------------------- #
    op.create_table(
        "audit_log",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),  # user.create | role.grant | so.confirm | ...
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before", postgresql.JSONB, nullable=True),
        sa.Column("after", postgresql.JSONB, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["tenant_id", "entity_type", "entity_id"])
    op.create_index("ix_audit_log_actor", "audit_log", ["tenant_id", "actor_id", "occurred_at"])


def downgrade() -> None:
    tables = [
        "audit_log",
        "feature_flags",
        "payment_intents",
        "webhook_deliveries",
        "webhook_subscriptions",
        "tenant_onboarding_log",
        "tenants",
    ]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
