"""identity: users, roles, permissions, role_permissions, user_roles

Revision ID: 0100_identity
Revises:
Create Date: 2026-07-11

Wave 0 — Foundations. See docs/SPEC.md#wave-0.

Note: RLS policies for these tables are added in the follow-up
migration `0101_identity_rls` so the table-create and the policy-create
are reviewable as separate units.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0100_identity"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_service_account", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "key", name="uq_roles_tenant_key"),
    )

    op.create_table(
        "permissions",
        sa.Column("key", sa.String(128), primary_key=True),  # global, not tenant-scoped (e.g. "erp.so.create")
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_key", sa.String(128), sa.ForeignKey("permissions.key", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_index("ix_role_permissions_permission_key", "role_permissions", ["permission_key"])
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    # Seed the global permission catalog. Tenant-scoped roles and role-permission
    # grants are seeded per-tenant in `provision_erp_tenant()` at tenant-onboard
    # time (W6).
    op.bulk_insert(
        sa.table(
            "permissions",
            sa.column("key", sa.String),
            sa.column("resource", sa.String),
            sa.column("action", sa.String),
            sa.column("description", sa.Text),
        ),
        [
            # Identity
            {"key": "identity.user.read", "resource": "user", "action": "read", "description": "Read user accounts"},
            {"key": "identity.user.write", "resource": "user", "action": "write", "description": "Create/update user accounts"},
            {"key": "identity.role.read", "resource": "role", "action": "read", "description": "Read roles and permissions"},
            {"key": "identity.role.write", "resource": "role", "action": "write", "description": "Create/update roles and grant permissions"},
            # Master data
            {"key": "master.party.read", "resource": "party", "action": "read", "description": "Read parties (customers/vendors/carriers)"},
            {"key": "master.party.write", "resource": "party", "action": "write", "description": "Create/update parties"},
            {"key": "master.product.read", "resource": "product", "action": "read", "description": "Read products / parts / BOMs"},
            {"key": "master.product.write", "resource": "product", "action": "write", "description": "Create/update products / BOMs"},
            {"key": "master.location.read", "resource": "location", "action": "read", "description": "Read warehouses / plants / addresses"},
            {"key": "master.location.write", "resource": "location", "action": "write", "description": "Create/update locations"},
            {"key": "master.price.read", "resource": "price", "action": "read", "description": "Read price lists and contracts"},
            {"key": "master.price.write", "resource": "price", "action": "write", "description": "Create/update price lists and contracts"},
            # O2C
            {"key": "o2c.so.read", "resource": "sales_order", "action": "read", "description": "Read sales orders"},
            {"key": "o2c.so.write", "resource": "sales_order", "action": "write", "description": "Create/update sales orders"},
            {"key": "o2c.so.confirm", "resource": "sales_order", "action": "confirm", "description": "Confirm a sales order"},
            {"key": "o2c.so.cancel", "resource": "sales_order", "action": "cancel", "description": "Cancel a confirmed sales order"},
            {"key": "o2c.credit.read", "resource": "credit", "action": "read", "description": "Read credit limit / hold state"},
            {"key": "o2c.credit.write", "resource": "credit", "action": "write", "description": "Override credit hold / adjust limit"},
            {"key": "o2c.invoice.read", "resource": "invoice", "action": "read", "description": "Read AR invoices"},
            {"key": "o2c.invoice.write", "resource": "invoice", "action": "write", "description": "Create / adjust AR invoices"},
            {"key": "o2c.payment.read", "resource": "payment", "action": "read", "description": "Read AR payments"},
            {"key": "o2c.payment.write", "resource": "payment", "action": "write", "description": "Apply / refund / write off payments"},
            # P2P
            {"key": "p2p.req.read", "resource": "requisition", "action": "read", "description": "Read purchase requisitions"},
            {"key": "p2p.req.write", "resource": "requisition", "action": "write", "description": "Create / update purchase requisitions"},
            {"key": "p2p.po.read", "resource": "purchase_order", "action": "read", "description": "Read purchase orders"},
            {"key": "p2p.po.write", "resource": "purchase_order", "action": "write", "description": "Create / update purchase orders"},
            {"key": "p2p.po.approve", "resource": "purchase_order", "action": "approve", "description": "Approve a purchase order"},
            {"key": "p2p.receipt.read", "resource": "receipt", "action": "read", "description": "Read goods receipts"},
            {"key": "p2p.receipt.write", "resource": "receipt", "action": "write", "description": "Adjust / reconcile goods receipts"},
            {"key": "p2p.ap.read", "resource": "ap_invoice", "action": "read", "description": "Read AP invoices"},
            {"key": "p2p.ap.write", "resource": "ap_invoice", "action": "write", "description": "Create / update AP invoices"},
            # Finance
            {"key": "finance.coa.read", "resource": "gl_account", "action": "read", "description": "Read chart of accounts"},
            {"key": "finance.coa.write", "resource": "gl_account", "action": "write", "description": "Create / update GL accounts"},
            {"key": "finance.journal.read", "resource": "journal", "action": "read", "description": "Read journal entries"},
            {"key": "finance.journal.write", "resource": "journal", "action": "write", "description": "Post / reverse journal entries"},
            {"key": "finance.period.close", "resource": "period", "action": "close", "description": "Open / close an accounting period"},
            {"key": "finance.tax.read", "resource": "tax", "action": "read", "description": "Read tax rates and exemptions"},
            {"key": "finance.tax.write", "resource": "tax", "action": "write", "description": "Update tax rates and exemptions"},
            # CRM
            {"key": "crm.contact.read", "resource": "contact", "action": "read", "description": "Read contacts"},
            {"key": "crm.contact.write", "resource": "contact", "action": "write", "description": "Create / update contacts"},
            {"key": "crm.lead.read", "resource": "lead", "action": "read", "description": "Read leads"},
            {"key": "crm.lead.write", "resource": "lead", "action": "write", "description": "Create / update / convert leads"},
            {"key": "crm.opp.read", "resource": "opportunity", "action": "read", "description": "Read opportunities"},
            {"key": "crm.opp.write", "resource": "opportunity", "action": "write", "description": "Create / update opportunities and pipeline stages"},
            {"key": "crm.quote.read", "resource": "quote", "action": "read", "description": "Read quotes"},
            {"key": "crm.quote.write", "resource": "quote", "action": "write", "description": "Create / update / send quotes"},
            {"key": "crm.ticket.read", "resource": "ticket", "action": "read", "description": "Read support tickets"},
            {"key": "crm.ticket.write", "resource": "ticket", "action": "write", "description": "Create / update / close tickets"},
            # Trade
            {"key": "trade.hts.read", "resource": "hts_schedule", "action": "read", "description": "Read HTS tariff schedules"},
            {"key": "trade.hts.write", "resource": "hts_schedule", "action": "write", "description": "Load / activate an HTS schedule version"},
            {"key": "trade.customs.read", "resource": "customs_entry", "action": "read", "description": "Read customs entries"},
            {"key": "trade.customs.write", "resource": "customs_entry", "action": "write", "description": "Create / file customs entries"},
            {"key": "trade.ftz.read", "resource": "ftz", "action": "read", "description": "Read FTZ inventory"},
            {"key": "trade.ftz.write", "resource": "ftz", "action": "write", "description": "Admit / remove FTZ inventory"},
            # Platform
            {"key": "platform.tenant.read", "resource": "tenant", "action": "read", "description": "Read tenant info"},
            {"key": "platform.tenant.write", "resource": "tenant", "action": "write", "description": "Onboard / suspend tenants"},
            {"key": "platform.connector.read", "resource": "connector", "action": "read", "description": "Read platform connectors"},
            {"key": "platform.connector.write", "resource": "connector", "action": "write", "description": "Connect / disconnect platforms"},
            {"key": "platform.webhook.read", "resource": "webhook", "action": "read", "description": "Read webhook subscriptions"},
            {"key": "platform.webhook.write", "resource": "webhook", "action": "write", "description": "Create / update webhook subscriptions"},
            # AI
            {"key": "ai.agent.run", "resource": "agent", "action": "run", "description": "Trigger an agent run"},
            {"key": "ai.rag.query", "resource": "rag", "action": "query", "description": "Run a RAG query"},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_index("ix_role_permissions_permission_key", table_name="role_permissions")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")
