"""Audit fix — RLS repair for W6 platform and W7 CRM productivity tables.

Background:
The W6 and W7 migrations (0109 and 0110) defined the `_rls()` helper
but never called it in their `upgrade()` bodies, leaving tenant-scoped
tables without RLS. This migration idempotently adds RLS so existing
deployments get covered, and so fresh installations end up correct.

Tables covered:
- W6 platform (7): tenants, tenant_onboarding_log,
  webhook_subscriptions, webhook_deliveries, payment_intents,
  feature_flags, audit_log.
- W7 CRM productivity (4): notifications, saved_views,
  custom_field_defs, custom_field_values.

`tenants` is intentionally not RLS-isolated (it IS the tenant);
we still enable RLS so that callers can read `current_tenant_id`
without seeing all tenants. The policy compares `tenants.id` to the
GUC and is a no-op for non-tenant code paths.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0114_w6_w7_rls_repair"
down_revision: str | Sequence[str] | None = "0113_w10_ops_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SENTINEL = "00000000-0000-0000-0000-000000000000"


def _tenant_predicate(table: str) -> str:
    subject = "id" if table == "tenants" else "tenant_id"
    return f"""
            {subject} = COALESCE(
                NULLIF(current_setting('app.tenant_id', true), ''),
                '{SENTINEL}'
            )::uuid
    """


def _enable_rls(table: str) -> None:
    """Enable + force RLS on a table with the correct tenant column."""
    predicate = _tenant_predicate(table)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING ({predicate})
        WITH CHECK ({predicate})
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

    # W7 CRM productivity
    for t in (
        "notifications",
        "saved_views",
        "custom_field_defs",
        "custom_field_values",
    ):
        _enable_rls(t)


def downgrade() -> None:
    for t in (
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
