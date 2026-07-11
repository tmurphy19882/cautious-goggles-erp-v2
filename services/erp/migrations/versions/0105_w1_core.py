"""W1 — master data + O2C core + finance (W1).

Revision ID: 0105_w1_core
Revises: 0104_outbox_events
Create Date: 2026-07-11

Wave 1 — O2C complete. Bundles the minimum tables needed for the
order-to-cash flow to actually run end-to-end:

- `products` — what SO lines point to
- `locations` — warehouses, plants, addresses (the O2C `ship_from_location_id`)
- `price_lists` + `price_list_items` — for `o2c.so.unit_price` lookup
- `credit_limits` — for the credit check
- `inventory_reservations` — for the reservation step
- `sales_orders` + `sales_order_lines` — the O2C root
- `invoices` + `invoice_lines` — AR (generated from SHIP_CONFIRMED)
- `payments` + `payment_applications` — AR cash application
- `inventory_valuation` — FIFO COGS snapshot
- `fx_rates` — multi-currency support
- `tax_rules` — basic tax engine
- `holds` — QMS-style holds on orders / parties

All tenant-scoped tables get FORCE RLS. Currency is `ISO 4217` (3-letter).
Money is `NUMERIC(20, 4)`. All UUID PKs.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0105_w1_core"
down_revision: str | Sequence[str] | None = "0104_outbox_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SENTINEL = "00000000-0000-0000-0000-000000000000"


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()"))


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
    # =====================================================================
    # Master data
    # =====================================================================

    op.create_table(
        "products",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("uom", sa.String(16), nullable=False, server_default="each"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
    )
    _rls("products")

    op.create_table(
        "locations",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),  # warehouse | plant | address | ftz
        sa.Column("address_line1", sa.String(255), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("region", sa.String(128), nullable=True),
        sa.Column("country_code", sa.String(3), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_locations_tenant_code"),
    )
    _rls("locations")

    op.create_table(
        "price_lists",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("effective_from", sa.Date, nullable=True),
        sa.Column("effective_to", sa.Date, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_price_lists_tenant_code"),
    )
    _rls("price_lists")

    op.create_table(
        "price_list_items",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("price_list_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("min_quantity", sa.Numeric(20, 4), nullable=False, server_default=sa.text("1")),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        *_timestamps(),
    )
    op.create_index("ix_price_list_items_list_product", "price_list_items", ["price_list_id", "product_id"])
    _rls("price_list_items")

    # =====================================================================
    # Finance: FX + tax
    # =====================================================================

    op.create_table(
        "fx_rates",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "base_currency", "quote_currency", "effective_date", name="uq_fx_rates_pair_date"),
    )
    _rls("fx_rates")

    op.create_table(
        "tax_rules",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("jurisdiction", sa.String(8), nullable=False),  # US-CA, US-NY, EU-DE, etc.
        sa.Column("rate_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("is_inclusive", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "code", name="uq_tax_rules_tenant_code"),
    )
    _rls("tax_rules")

    # =====================================================================
    # O2C: credit + reservation + sales_order
    # =====================================================================

    op.create_table(
        "credit_limits",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("limit_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("exposure_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("hold_reason", sa.String(255), nullable=True),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("held_by", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "customer_id", "currency", name="uq_credit_limits_tenant_customer_currency"),
    )
    _rls("credit_limits")

    op.create_table(
        "inventory_reservations",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        # active → fulfilled | released
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_reason", sa.String(255), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_inventory_reservations_order_product", "inventory_reservations", ["sales_order_id", "product_id"]
    )
    _rls("inventory_reservations")

    op.create_table(
        "sales_orders",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("order_number", sa.String(32), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        # draft → credit_hold → confirmed → invoiced → paid → closed
        #                       \→ cancelled
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("fx_rate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fx_rates.id"), nullable=True),
        sa.Column("subtotal", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("cogs_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.String(255), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),  # set on invoice creation
        sa.Column("notes", sa.Text, nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "order_number", name="uq_sales_orders_tenant_number"),
    )
    op.create_index("ix_sales_orders_customer", "sales_orders", ["customer_id", "status"])
    op.create_index("ix_sales_orders_status_created", "sales_orders", ["status", "created_at"])
    _rls("sales_orders")

    op.create_table(
        "sales_order_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),  # denormalised for event payloads
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default="each"),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_rule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tax_rules.id"), nullable=True),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("line_total", sa.Numeric(20, 4), nullable=False),  # (qty * unit_price * (1 - disc)) + tax
        sa.Column("ship_from_location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=True),
        sa.Column("cogs_unit_cost", sa.Numeric(20, 4), nullable=True),  # filled at invoice
        sa.Column("cogs_total", sa.Numeric(20, 4), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("sales_order_id", "line_number", name="uq_so_lines_order_line"),
    )
    _rls("sales_order_lines")

    # =====================================================================
    # O2C: invoice + payment
    # =====================================================================

    op.create_table(
        "invoices",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("invoice_number", sa.String(32), nullable=False),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_orders.id"), nullable=False, index=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        # open → partially_paid → paid → void
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal", sa.Numeric(20, 4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("total_amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("cogs_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("amount_paid", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "invoice_number", name="uq_invoices_tenant_number"),
    )
    op.create_index("ix_invoices_customer_status", "invoices", ["customer_id", "status"])
    _rls("invoices")

    op.create_table(
        "invoice_lines",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sales_order_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_order_lines.id"), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default="each"),
        sa.Column("unit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("line_total", sa.Numeric(20, 4), nullable=False),
        sa.Column("cogs_unit_cost", sa.Numeric(20, 4), nullable=True),
        sa.Column("cogs_total", sa.Numeric(20, 4), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("invoice_id", "line_number", name="uq_invoice_lines_invoice_line"),
    )
    _rls("invoice_lines")

    op.create_table(
        "payments",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="wire"),  # wire|ach|check|card
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("unapplied_amount", sa.Numeric(20, 4), nullable=False),  # amount minus applied
        *_timestamps(),
    )
    op.create_index("ix_payments_customer", "payments", ["customer_id"])
    _rls("payments")

    op.create_table(
        "payment_applications",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoices.id"), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        *_timestamps(),
    )
    _rls("payment_applications")

    # =====================================================================
    # O2C: inventory valuation (FIFO layers)
    # =====================================================================

    op.create_table(
        "inventory_valuation",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("layer_date", sa.DateTime(timezone=True), nullable=False),  # FIFO order
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),  # purchase | adjustment | opening
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("depleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("depleted_by_invoice_line_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("invoice_lines.id"), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_inventory_valuation_fifo",
        "inventory_valuation",
        ["product_id", "location_id", "layer_date"],
    )
    _rls("inventory_valuation")

    # =====================================================================
    # Holds (QMS-style)
    # =====================================================================

    op.create_table(
        "holds",
        _uuid_pk(),
        _tenant_uuid(),
        sa.Column("subject_type", sa.String(32), nullable=False),  # sales_order | customer | product
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),  # qms | manual | credit
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("placed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("released_reason", sa.String(255), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_holds_subject", "holds", ["subject_type", "subject_id", "released_at"])
    _rls("holds")


def downgrade() -> None:
    for table in (
        "holds",
        "inventory_valuation",
        "payment_applications",
        "payments",
        "invoice_lines",
        "invoices",
        "sales_order_lines",
        "sales_orders",
        "inventory_reservations",
        "credit_limits",
        "tax_rules",
        "fx_rates",
        "price_list_items",
        "price_lists",
        "locations",
        "products",
    ):
        _drop_rls(table)
        op.drop_table(table)
