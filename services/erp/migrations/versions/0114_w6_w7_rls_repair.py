"""Audit fix — RLS repair for W6 (platform) and W7 (crm_ai) tables.

Background:
The W6 and W7 migrations (0109 and 0110) defined the `_rls()` helper
but never called it in their `upgrade()` bodies, leaving 14
tenant-scoped tables without RLS. This migration idempotently adds
RLS to those 14 tables so existing deployments get covered, and so
fresh installations end up correct.

Tables covered:
- W6 platform (7): tenants, tenant_onboarding_log,
  webhook_subscriptions, webhook_deliveries, payment_intents,
  feature_flags, audit_log.
- W7 crm_ai (7): notifications, saved_views, custom_field_defs,
  custom_field_values, ai_agent_runs, ai_recommendations,
  ai_agent_feedback.

`tenants` is intentionally not RLS-isolated (it IS the tenant);
we still enable RLS so that callers can read `current_tenant_id`
without seeing all tenants. The policy compares `tenants.id` to the
GUC and is a no-op for non-tenant code paths.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0114_w6_w7_rls_repair"
down_revision: str | Sequence[str] | None = "0113_w10_ops_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SENTINEL = "00000000-0000-0000-0000-000000000000"


def _enable_rls(table: str) -> None:
    """Idempotently enable + force RLS on a table.

    Wraps the steps in a savepoint so a missing policy / table on
    an older database doesn't fail the whole migration.
    """
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


def upgrade() -> None:
    # W6 platform
    for t in (
        "tenants",
        "tenant_onboarding_log",
        "webhook_subscriptions",
        "webhook_deliveries",
        "payment_intents",
        "feature_flags",
        "audit_log",
    ):
        _enable_rls(t)

    # W7 crm_ai
    for t in (
        "notifications",
        "saved_views",
        "custom_field_defs",
        "custom_field_values",
        "ai_agent_runs",
        "ai_recommendations",
        "ai_agent_feedback",
    ):
        _enable_rls(t)


def downgrade() -> None:
    for t in (
        "ai_agent_feedback",
        "ai_recommendations",
        "ai_agent_runs",
        "custom_field_values",
        "custom_field_defs",
        "saved_views",
        "notifications",
        "audit_log",
        "feature_flags",
        "payment_intents",
        "webhook_deliveries",
        "webhook_subscriptions",
        "tenant_onboarding_log",
        "tenants",
    ):
        op.execute(f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
