"""P2P core migration (Wave 2) — purchase requisitions, POs, receipts,
3-way match, AP invoices, tax + approval.

Closes the W2 punch list. All tenant-scoped tables with FORCE RLS
via the SENTINEL COALESCE pattern.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0106_w2_p2p"
down_revision: str | Sequence[str] | None = "0105_w1_core"
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
    # ----- suppliers (party type=vendor) ------------------------------- #
    op.create_table(
        "suppliers",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("supplier_code", sa.String(64), nullable=False),
        sa.Column("tax_id", sa.String(64), nullable=True),
        sa.Column("payment_terms", sa.String(32), nullable=False, server_default="net30"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_suspended", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("suspended_reason", sa.String(255), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_suppliers_tenant_user"),
        sa.UniqueConstraint("tenant_id", "supplier_code", name="uq_suppliers_tenant_code"),
    )
    _rls("suppliers")

    # ----- purchase requisitions -------------------------------------- #
    op.create_table(
        "purchase_requisitions",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("requisition_number", sa.String(32), nullable=False),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        # draft → approved → converted → rejected
        sa.Column("justification", sa.Text, nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(255), nullable=True),
        sa.Column("converted_po_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "requisition_number", name="uq_pr_tenant_number"),
    )
    _rls("purchase_requisitions")

    op.create_table(
        "purchase_requisition_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("requisition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_requisitions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default="each"),
        sa.Column("estimated_unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("needed_by", sa.Date, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("requisition_id", "line_number", name="uq_pr_lines_pr_line"),
    )
    _rls("purchase_requisition_lines")

    # ----- purchase orders --------------------------------------------- #
    op.create_table(
        "purchase_orders",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("po_number", sa.String(32), nullable=False),
        sa.Column("requisition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_requisitions.id"), nullable=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        # draft → pending_approval → approved → sent → partially_received → received → closed
        #       ↘ rejected / cancelled
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("fx_rate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fx_rates.id"), nullable=True),
        sa.Column("subtotal", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("payment_terms", sa.String(32), nullable=False, server_default="net30"),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_method", sa.String(32), nullable=True),  # email | edi | portal
        sa.Column("expected_delivery", sa.Date, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "po_number", name="uq_po_tenant_number"),
    )
    op.create_index("ix_purchase_orders_supplier_status", "purchase_orders", ["supplier_id", "status"])
    _rls("purchase_orders")

    op.create_table(
        "purchase_order_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default="each"),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("tax_rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tax_rules.id"), nullable=True),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("line_total", sa.Numeric(20, 4), nullable=False),
        sa.Column("quantity_received", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity_invoiced", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        *_timestamps(),
        sa.UniqueConstraint("po_id", "line_number", name="uq_po_lines_po_line"),
    )
    _rls("purchase_order_lines")

    # ----- PO approval (SoD: approver ≠ requester) -------------------- #
    op.create_table(
        "po_approvals",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),  # approved | rejected
        sa.Column("threshold_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reason", sa.String(255), nullable=True),
        *_timestamps(),
    )
    _rls("po_approvals")

    # ----- goods receipts (WMS-driven) -------------------------------- #
    op.create_table(
        "goods_receipts",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("receipt_number", sa.String(32), nullable=False),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=False, index=True),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("received_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        # received → matched → exception
        sa.Column("notes", sa.Text, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "receipt_number", name="uq_receipts_tenant_number"),
    )
    _rls("goods_receipts")

    op.create_table(
        "goods_receipt_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_order_lines.id"), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("quantity_received", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default="each"),
        sa.Column("lot_id", sa.String(64), nullable=True),
        sa.Column("bin_id", sa.String(64), nullable=True),
        sa.Column("quality_status", sa.String(32), nullable=False, server_default="accepted"),
        # accepted | hold | rejected
        *_timestamps(),
        sa.UniqueConstraint("receipt_id", "line_number", name="uq_receipt_lines_receipt_line"),
    )
    _rls("goods_receipt_lines")

    # ----- 3-way match -------------------------------------------------- #
    op.create_table(
        "three_way_matches",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=False, index=True),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goods_receipts.id"), nullable=False),
        sa.Column("ap_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),  # filled when AP is recorded
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        # pending → matched → exception → released
        sa.Column("quantity_tolerance_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("2.0")),
        sa.Column("price_tolerance_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("2.0")),
        sa.Column("quantity_exception", sa.Text, nullable=True),
        sa.Column("price_exception", sa.Text, nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
    )
    _rls("three_way_matches")

    op.create_table(
        "three_way_match_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("three_way_matches.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_order_lines.id"), nullable=False),
        sa.Column("receipt_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goods_receipt_lines.id"), nullable=False),
        sa.Column("quantity_ordered", sa.Numeric(20, 4), nullable=False),
        sa.Column("quantity_received", sa.Numeric(20, 4), nullable=False),
        sa.Column("quantity_invoiced", sa.Numeric(20, 4), nullable=True),
        sa.Column("unit_price_ordered", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_price_invoiced", sa.Numeric(20, 4), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="matched"),
        # matched | qty_short | qty_over | price_mismatch | qty_and_price
        *_timestamps(),
    )
    _rls("three_way_match_lines")

    # ----- AP invoices -------------------------------------------------- #
    op.create_table(
        "ap_invoices",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("invoice_number", sa.String(64), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False, index=True),
        sa.Column("po_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        # open → matched → approved → partially_paid → paid → disputed
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(20, 4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("amount_paid", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("supplier_invoice_date", sa.Date, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispute_reason", sa.String(255), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "invoice_number", name="uq_ap_invoices_tenant_number"),
    )
    _rls("ap_invoices")

    op.create_table(
        "ap_invoice_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("ap_invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ap_invoices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("po_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_order_lines.id"), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("line_total", sa.Numeric(20, 4), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("ap_invoice_id", "line_number", name="uq_ap_invoice_lines_invoice_line"),
    )
    _rls("ap_invoice_lines")

    # ----- AP payments (supplier cash-out) ----------------------------- #
    op.create_table(
        "ap_payments",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False, index=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="ach"),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("unapplied_amount", sa.Numeric(20, 4), nullable=False),
        *_timestamps(),
    )
    _rls("ap_payments")

    op.create_table(
        "ap_payment_applications",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ap_payments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ap_invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ap_invoices.id"), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        *_timestamps(),
    )
    _rls("ap_payment_applications")


def downgrade() -> None:
    tables = [
        "ap_payment_applications",
        "ap_payments",
        "ap_invoice_lines",
        "ap_invoices",
        "three_way_match_lines",
        "three_way_matches",
        "goods_receipt_lines",
        "goods_receipts",
        "po_approvals",
        "purchase_order_lines",
        "purchase_orders",
        "purchase_requisition_lines",
        "purchase_requisitions",
        "suppliers",
    ]
    for table in tables:
        _drop_rls(table)
        op.drop_table(table)
