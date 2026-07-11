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
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

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
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
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
            ("identity.user.read", "user", "read", "Read user accounts"),
            ("identity.user.write", "user", "write", "Create/update user accounts"),
            ("identity.role.read", "role", "read", "Read roles and permissions"),
            ("identity.role.write", "role", "write", "Create/update roles and grant permissions"),
            # Master data
            ("master.party.read", "party", "read", "Read parties (customers/vendors/carriers)"),
            ("master.party.write", "party", "write", "Create/update parties"),
            ("master.product.read", "product", "read", "Read products / parts / BOMs"),
            ("master.product.write", "product", "write", "Create/update products / BOMs"),
            ("master.location.read", "location", "read", "Read warehouses / plants / addresses"),
            ("master.location.write", "location", "write", "Create/update locations"),
            ("master.price.read", "price", "read", "Read price lists and contracts"),
            ("master.price.write", "price", "write", "Create/update price lists and contracts"),
            # O2C
            ("o2c.so.read", "sales_order", "read", "Read sales orders"),
            ("o2c.so.write", "sales_order", "write", "Create/update sales orders"),
            ("o2c.so.confirm", "sales_order", "confirm", "Confirm a sales order"),
            ("o2c.so.cancel", "sales_order", "cancel", "Cancel a confirmed sales order"),
            ("o2c.credit.read", "credit", "read", "Read credit limit / hold state"),
            ("o2c.credit.write", "credit", "write", "Override credit hold / adjust limit"),
            ("o2c.invoice.read", "invoice", "read", "Read AR invoices"),
            ("o2c.invoice.write", "invoice", "write", "Create / adjust AR invoices"),
            ("o2c.payment.read", "payment", "read", "Read AR payments"),
            ("o2c.payment.write", "payment", "write", "Apply / refund / write off payments"),
            # P2P
            ("p2p.req.read", "requisition", "read", "Read purchase requisitions"),
            ("p2p.req.write", "requisition", "write", "Create / update purchase requisitions"),
            ("p2p.po.read", "purchase_order", "read", "Read purchase orders"),
            ("p2p.po.write", "purchase_order", "write", "Create / update purchase orders"),
            ("p2p.po.approve", "purchase_order", "approve", "Approve a purchase order"),
            ("p2p.receipt.read", "receipt", "read", "Read goods receipts"),
            ("p2p.receipt.write", "receipt", "write", "Adjust / reconcile goods receipts"),
            ("p2p.ap.read", "ap_invoice", "read", "Read AP invoices"),
            ("p2p.ap.write", "ap_invoice", "write", "Create / update AP invoices"),
            # Finance
            ("finance.coa.read", "gl_account", "read", "Read chart of accounts"),
            ("finance.coa.write", "gl_account", "write", "Create / update GL accounts"),
            ("finance.journal.read", "journal", "read", "Read journal entries"),
            ("finance.journal.write", "journal", "write", "Post / reverse journal entries"),
            ("finance.period.close", "period", "close", "Open / close an accounting period"),
            ("finance.tax.read", "tax", "read", "Read tax rates and exemptions"),
            ("finance.tax.write", "tax", "write", "Update tax rates and exemptions"),
            # CRM
            ("crm.contact.read", "contact", "read", "Read contacts"),
            ("crm.contact.write", "contact", "write", "Create / update contacts"),
            ("crm.lead.read", "lead", "read", "Read leads"),
            ("crm.lead.write", "lead", "write", "Create / update / convert leads"),
            ("crm.opp.read", "opportunity", "read", "Read opportunities"),
            ("crm.opp.write", "opportunity", "write", "Create / update opportunities and pipeline stages"),
            ("crm.quote.read", "quote", "read", "Read quotes"),
            ("crm.quote.write", "quote", "write", "Create / update / send quotes"),
            ("crm.ticket.read", "ticket", "read", "Read support tickets"),
            ("crm.ticket.write", "ticket", "write", "Create / update / close tickets"),
            # Trade
            ("trade.hts.read", "hts_schedule", "read", "Read HTS tariff schedules"),
            ("trade.hts.write", "hts_schedule", "write", "Load / activate an HTS schedule version"),
            ("trade.customs.read", "customs_entry", "read", "Read customs entries"),
            ("trade.customs.write", "customs_entry", "write", "Create / file customs entries"),
            ("trade.ftz.read", "ftz", "read", "Read FTZ inventory"),
            ("trade.ftz.write", "ftz", "write", "Admit / remove FTZ inventory"),
            # Platform
            ("platform.tenant.read", "tenant", "read", "Read tenant info"),
            ("platform.tenant.write", "tenant", "write", "Onboard / suspend tenants"),
            ("platform.connector.read", "connector", "read", "Read platform connectors"),
            ("platform.connector.write", "connector", "write", "Connect / disconnect platforms"),
            ("platform.webhook.read", "webhook", "read", "Read webhook subscriptions"),
            ("platform.webhook.write", "webhook", "write", "Create / update webhook subscriptions"),
            # AI
            ("ai.agent.run", "agent", "run", "Trigger an agent run"),
            ("ai.rag.query", "rag", "query", "Run a RAG query"),
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
