"""Invoice generation for O2C (O2C-6).

`generate_invoice_for_order` is called by the SHIP_CONFIRMED consumer
(or by `confirm_and_force_invoice` in tests). It creates an
`invoices` row, one `invoice_lines` row per SO line, and emits
`INVOICE_GENERATED` on the outbox.

`apply_payment` is the cash-application side (O2C-7): take a
`payments` row, apply it to one or more invoices, update AR
status, emit `PAYMENT_RECEIVED` on the outbox.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from o2c.cogs import allocate_cogs_for_invoice
from o2c.tax_fx import _q
from shared.events import (
    TOPIC_INVOICE_GENERATED,
    TOPIC_PAYMENT_RECEIVED,
    build_envelope,
    customer_token,
)
from shared.outbox import OutboxStore
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GenerateInvoiceResult:
    invoice_id: UUID
    invoice_number: str
    event_id: UUID
    total_amount: Decimal
    cogs_amount: Decimal
    line_count: int


@dataclass(slots=True)
class ApplyPaymentInput:
    payment_id: UUID


@dataclass(slots=True)
class ApplyPaymentResult:
    payment_id: UUID
    applied_amount: Decimal
    unapplied_amount: Decimal
    invoices_affected: list[UUID] = field(default_factory=list)
    event_id: UUID | None = None


async def generate_invoice_for_order(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    sales_order_id: UUID,
    trace_id: str | None = None,
) -> GenerateInvoiceResult:
    """Create an invoice + lines for a confirmed SO. Idempotent on
    `sales_order.invoice_id` — if the SO already has an invoice, the
    existing one is returned.
    """
    outbox = OutboxStore(session)

    # Load the SO.
    order_row = (
        await session.execute(
            _sa_text(
                "SELECT * FROM sales_orders WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"id": sales_order_id, "tenant_id": tenant_id},
        )
    ).mappings().first()
    if order_row is None:
        raise ValueError(f"sales order not found: {sales_order_id}")
    if order_row["invoice_id"] is not None:
        # Idempotent: return the existing invoice.
        existing = (
            await session.execute(
                _sa_text(
                    "SELECT * FROM invoices WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": order_row["invoice_id"], "tenant_id": tenant_id},
            )
        ).mappings().first()
        if existing is not None:
            return GenerateInvoiceResult(
                invoice_id=existing["id"],
                invoice_number=existing["invoice_number"],
                event_id=uuid4(),  # placeholder
                total_amount=Decimal(str(existing["total_amount"])),
                cogs_amount=Decimal(str(existing["cogs_amount"])),
                line_count=0,
            )

    if order_row["status"] != "confirmed":
        raise ValueError(
            f"cannot invoice order in status {order_row['status']}; must be confirmed"
        )

    # Load the lines.
    line_rows = (
        await session.execute(
            _sa_text(
                "SELECT * FROM sales_order_lines WHERE sales_order_id = :id "
                "AND tenant_id = :tenant_id ORDER BY line_number"
            ),
            {"id": sales_order_id, "tenant_id": tenant_id},
        )
    ).mappings().all()

    # Allocate COGS via FIFO (O2C-9).
    cogs_allocations = await allocate_cogs_for_invoice(
        session,
        tenant_id=tenant_id,
        lines=[dict(r) for r in line_rows],
    )
    total_cogs = sum((c.cogs_total for c in cogs_allocations), Decimal("0"))

    # Insert the invoice.
    invoice_id = uuid4()
    invoice_number = f"INV-{invoice_id.hex[:8].upper()}"
    await session.execute(
        _sa_text(
            """
            INSERT INTO invoices (
                id, tenant_id, invoice_number, sales_order_id, customer_id,
                status, currency, subtotal, tax_amount, total_amount,
                cogs_amount
            ) VALUES (
                :id, :tenant_id, :invoice_number, :sales_order_id, :customer_id,
                'open', :currency, :subtotal, :tax_amount, :total_amount,
                :cogs_amount
            )
            """
        ),
        {
            "id": invoice_id,
            "tenant_id": tenant_id,
            "invoice_number": invoice_number,
            "sales_order_id": sales_order_id,
            "customer_id": order_row["customer_id"],
            "currency": order_row["currency"],
            "subtotal": order_row["subtotal"],
            "tax_amount": order_row["tax_amount"],
            "total_amount": order_row["total_amount"],
            "cogs_amount": total_cogs,
        },
    )

    # Insert invoice lines (mirror the SO lines, with cogs back-filled).
    for so_line, cogs_alloc in zip(line_rows, cogs_allocations):
        await session.execute(
            _sa_text(
                """
                INSERT INTO invoice_lines (
                    tenant_id, invoice_id, sales_order_line_id, line_number,
                    sku, description, quantity, unit, unit_price, tax_amount,
                    line_total, cogs_unit_cost, cogs_total
                ) VALUES (
                    :tenant_id, :invoice_id, :so_line_id, :line_number,
                    :sku, :description, :quantity, :unit, :unit_price, :tax_amount,
                    :line_total, :cogs_unit_cost, :cogs_total
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_id,
                "so_line_id": so_line["id"],
                "line_number": so_line["line_number"],
                "sku": so_line["sku"],
                "description": so_line["description"],
                "quantity": so_line["quantity"],
                "unit": so_line["unit"],
                "unit_price": so_line["unit_price"],
                "tax_amount": so_line["tax_amount"],
                "line_total": so_line["line_total"],
                "cogs_unit_cost": cogs_alloc.unit_cost,
                "cogs_total": cogs_alloc.cogs_total,
            },
        )

    # Back-fill the SO with the invoice id and update line cogs.
    await session.execute(
        _sa_text(
            "UPDATE sales_orders SET invoice_id = :invoice_id, "
            "cogs_amount = :cogs, status = 'invoiced', updated_at = :now "
            "WHERE id = :id"
        ),
        {
            "invoice_id": invoice_id,
            "cogs": total_cogs,
            "now": utcnow(),
            "id": sales_order_id,
        },
    )
    for so_line, cogs_alloc in zip(line_rows, cogs_allocations):
        await session.execute(
            _sa_text(
                "UPDATE sales_order_lines SET cogs_unit_cost = :u, cogs_total = :t, updated_at = :now "
                "WHERE id = :id"
            ),
            {
                "u": cogs_alloc.unit_cost,
                "t": cogs_alloc.cogs_total,
                "now": utcnow(),
                "id": so_line["id"],
            },
        )

    # O2C-14: emit INVOICE_GENERATED.
    envelope = build_envelope(
        event_type=TOPIC_INVOICE_GENERATED,
        tenant_id=tenant_id,
        trace_id=trace_id or "n/a",
        payload={
            "invoice_id": str(invoice_id),
            "invoice_number": invoice_number,
            "sales_order_id": str(sales_order_id),
            "customer_token": customer_token(order_row["customer_id"]),
            "currency": order_row["currency"],
            "subtotal": str(order_row["subtotal"]),
            "tax_amount": str(order_row["tax_amount"]),
            "total_amount": str(order_row["total_amount"]),
            "cogs_amount": str(total_cogs),
        },
    )
    event_id = UUID(envelope.event_id) if isinstance(envelope.event_id, str) else envelope.event_id
    await outbox.emit(
        tenant_id=tenant_id,
        aggregate_type="invoice",
        aggregate_id=invoice_id,
        envelope=envelope,
        topic=TOPIC_INVOICE_GENERATED,
    )
    await session.flush()

    return GenerateInvoiceResult(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        event_id=event_id,
        total_amount=Decimal(str(order_row["total_amount"])),
        cogs_amount=_q(total_cogs),
        line_count=len(line_rows),
    )


async def apply_payment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    payment_id: UUID,
    target_invoice_id: UUID | None = None,
    amount: Decimal | None = None,
    trace_id: str | None = None,
) -> ApplyPaymentResult:
    """Apply a payment to one invoice (or FIFO across open invoices
    if `target_invoice_id` is None).
    """
    outbox = OutboxStore(session)
    pay_row = (
        await session.execute(
            _sa_text("SELECT * FROM payments WHERE id = :id AND tenant_id = :tenant_id"),
            {"id": payment_id, "tenant_id": tenant_id},
        )
    ).mappings().first()
    if pay_row is None:
        raise ValueError(f"payment not found: {payment_id}")
    unapplied = Decimal(str(pay_row["unapplied_amount"]))
    if unapplied <= Decimal("0"):
        return ApplyPaymentResult(
            payment_id=payment_id,
            applied_amount=Decimal("0"),
            unapplied_amount=Decimal("0"),
        )
    to_apply = amount if amount is not None else unapplied
    if to_apply > unapplied:
        raise ValueError(
            f"cannot apply {to_apply}; only {unapplied} unapplied on payment {payment_id}"
        )

    if target_invoice_id is not None:
        inv_ids = [target_invoice_id]
    else:
        # FIFO across open invoices for the customer.
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT id, total_amount, amount_paid, currency "
                    "FROM invoices WHERE tenant_id = :tenant_id AND customer_id = :customer_id "
                    "AND status IN ('open', 'partially_paid') "
                    "ORDER BY issued_at"
                ),
                {"tenant_id": tenant_id, "customer_id": pay_row["customer_id"]},
            )
        ).mappings().all()
        inv_ids = [r["id"] for r in rows]

    affected: list[UUID] = []
    remaining = to_apply
    for inv_id in inv_ids:
        if remaining <= Decimal("0"):
            break
        inv = (
            await session.execute(
                _sa_text(
                    "SELECT id, total_amount, amount_paid, currency "
                    "FROM invoices WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": inv_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if inv is None:
            continue
        outstanding = Decimal(str(inv["total_amount"])) - Decimal(str(inv["amount_paid"]))
        if outstanding <= Decimal("0"):
            continue
        apply_amount = min(remaining, outstanding)
        await session.execute(
            _sa_text(
                """
                INSERT INTO payment_applications (
                    tenant_id, payment_id, invoice_id, amount
                ) VALUES (:tenant_id, :payment_id, :invoice_id, :amount)
                """
            ),
            {
                "tenant_id": tenant_id,
                "payment_id": payment_id,
                "invoice_id": inv_id,
                "amount": apply_amount,
            },
        )
        new_paid = Decimal(str(inv["amount_paid"])) + apply_amount
        new_status = (
            "paid" if new_paid >= Decimal(str(inv["total_amount"])) else "partially_paid"
        )
        await session.execute(
            _sa_text(
                "UPDATE invoices SET amount_paid = :paid, status = :status, updated_at = :now "
                "WHERE id = :id"
            ),
            {"paid": new_paid, "status": new_status, "now": utcnow(), "id": inv_id},
        )
        remaining -= apply_amount
        affected.append(inv_id)

    applied = to_apply - remaining
    new_unapplied = unapplied - applied
    await session.execute(
        _sa_text("UPDATE payments SET unapplied_amount = :u WHERE id = :id"),
        {"u": new_unapplied, "id": payment_id},
    )

    # O2C-14: emit PAYMENT_RECEIVED.
    envelope = build_envelope(
        event_type=TOPIC_PAYMENT_RECEIVED,
        tenant_id=tenant_id,
        trace_id=trace_id or "n/a",
        payload={
            "payment_id": str(payment_id),
            "customer_id": str(pay_row["customer_id"]),
            "currency": pay_row["currency"],
            "amount": str(applied),
            "unapplied_amount": str(new_unapplied),
            "invoice_ids": [str(i) for i in affected],
        },
    )
    event_id = UUID(envelope.event_id) if isinstance(envelope.event_id, str) else envelope.event_id
    await outbox.emit(
        tenant_id=tenant_id,
        aggregate_type="payment",
        aggregate_id=payment_id,
        envelope=envelope,
        topic=TOPIC_PAYMENT_RECEIVED,
    )
    await session.flush()

    return ApplyPaymentResult(
        payment_id=payment_id,
        applied_amount=applied,
        unapplied_amount=new_unapplied,
        invoices_affected=affected,
        event_id=event_id,
    )
