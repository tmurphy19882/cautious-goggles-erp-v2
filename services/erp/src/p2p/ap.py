"""AP (Accounts Payable) service for P2P (P2P-6).

W2's AP flow:
1. Supplier sends an invoice (in production: 810/EDI; in W1: POST endpoint).
2. `record_ap_invoice` creates the `ap_invoices` row + lines.
3. If the invoice is linked to a PO, the 3-way match's `ap_invoice_id`
   is updated; if the match is `matched`, AP can be approved; if
   `exception`, AP is held.
4. `apply_ap_payment` records a payment against the AP invoice.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from o2c.tax_fx import compute_line_totals, resolve_tax_rule
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class APInvoiceLineInput:
    po_line_id: UUID
    sku: str
    description: str | None
    quantity: Decimal
    unit_price: Decimal
    tax_rule_id: UUID | None = None


@dataclass(slots=True)
class RecordAPInvoiceInput:
    invoice_number: str
    supplier_id: UUID
    po_id: UUID | None = None
    currency: str = "USD"
    supplier_invoice_date: Any | None = None
    due_date: Any | None = None
    lines: list[APInvoiceLineInput] = field(default_factory=list)


@dataclass(slots=True)
class RecordAPInvoiceResult:
    ap_invoice_id: UUID
    status: str
    total_amount: Decimal


class APService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_invoice(
        self, *, tenant_id: UUID, input: RecordAPInvoiceInput
    ) -> RecordAPInvoiceResult:
        if not input.lines:
            raise ValueError("at least one line is required")
        # Compute totals.
        subtotal = Decimal("0")
        tax_amount = Decimal("0")
        line_rows: list[dict[str, Any]] = []
        for idx, line in enumerate(input.lines, start=1):
            rule = await resolve_tax_rule(self._session, tenant_id, line.tax_rule_id)
            line_total, line_tax = compute_line_totals(
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_pct=Decimal("0"),
                tax_rate_pct=rule.rate_pct if rule else Decimal("0"),
                is_inclusive=rule.is_inclusive if rule else False,
            )
            subtotal += line_total - line_tax
            tax_amount += line_tax
            line_rows.append(
                {
                    "line_number": idx,
                    "po_line_id": line.po_line_id,
                    "sku": line.sku,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "tax_amount": line_tax,
                    "line_total": line_total,
                }
            )
        total = subtotal + tax_amount

        apid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO ap_invoices (
                    id, tenant_id, invoice_number, supplier_id, po_id,
                    status, currency, subtotal, tax_amount, total_amount,
                    supplier_invoice_date, due_date
                ) VALUES (
                    :id, :tenant_id, :inv, :supplier_id, :po_id,
                    'open', :currency, :subtotal, :tax, :total,
                    :inv_date, :due
                )
                """
            ),
            {
                "id": apid,
                "tenant_id": tenant_id,
                "inv": input.invoice_number,
                "supplier_id": input.supplier_id,
                "po_id": input.po_id,
                "currency": input.currency,
                "subtotal": subtotal,
                "tax": tax_amount,
                "total": total,
                "inv_date": input.supplier_invoice_date,
                "due": input.due_date,
            },
        )
        for row in line_rows:
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO ap_invoice_lines (
                        tenant_id, ap_invoice_id, po_line_id, line_number, sku,
                        description, quantity, unit_price, tax_amount, line_total
                    ) VALUES (
                        :tenant_id, :apid, :po_line_id, :ln, :sku,
                        :description, :quantity, :unit_price, :tax_amount, :line_total
                    )
                    """
                ),
                {"tenant_id": tenant_id, "apid": apid, **row},
            )
        # If linked to a PO, link the existing 3-way match.
        if input.po_id is not None:
            await self._session.execute(
                _sa_text(
                    "UPDATE three_way_matches SET ap_invoice_id = :apid, updated_at = :now "
                    "WHERE po_id = :po_id AND tenant_id = :tenant_id"
                ),
                {"apid": apid, "now": utcnow(), "po_id": input.po_id, "tenant_id": tenant_id},
            )
        # Bump quantity_invoiced on the PO line.
        for row in line_rows:
            await self._session.execute(
                _sa_text(
                    "UPDATE purchase_order_lines SET quantity_invoiced = quantity_invoiced + :qty "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"qty": row["quantity"], "id": row["po_line_id"], "tenant_id": tenant_id},
            )
        await self._session.flush()
        return RecordAPInvoiceResult(
            ap_invoice_id=apid, status="open", total_amount=total
        )

    async def apply_payment(
        self, *, tenant_id: UUID, ap_invoice_id: UUID, amount: Decimal, method: str = "ach"
    ) -> dict[str, Any]:
        inv = (
            await self._session.execute(
                _sa_text(
                    "SELECT * FROM ap_invoices WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": ap_invoice_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if inv is None:
            raise ValueError(f"ap invoice not found: {ap_invoice_id}")
        outstanding = Decimal(str(inv["total_amount"])) - Decimal(str(inv["amount_paid"]))
        if amount > outstanding:
            raise ValueError(f"cannot apply {amount}; only {outstanding} outstanding")

        pay_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO ap_payments (
                    id, tenant_id, supplier_id, currency, amount, method, unapplied_amount
                ) VALUES (:id, :tenant_id, :sid, :currency, :amount, :method, 0)
                """
            ),
            {
                "id": pay_id,
                "tenant_id": tenant_id,
                "sid": inv["supplier_id"],
                "currency": inv["currency"],
                "amount": amount,
                "method": method,
            },
        )
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO ap_payment_applications (
                    tenant_id, payment_id, ap_invoice_id, amount
                ) VALUES (:tenant_id, :pay_id, :apid, :amount)
                """
            ),
            {"tenant_id": tenant_id, "pay_id": pay_id, "apid": ap_invoice_id, "amount": amount},
        )
        new_paid = Decimal(str(inv["amount_paid"])) + amount
        new_status = "paid" if new_paid >= Decimal(str(inv["total_amount"])) else "partially_paid"
        await self._session.execute(
            _sa_text(
                "UPDATE ap_invoices SET amount_paid = :paid, status = :status, updated_at = :now "
                "WHERE id = :id"
            ),
            {"paid": new_paid, "status": new_status, "now": utcnow(), "id": ap_invoice_id},
        )
        return {
            "payment_id": str(pay_id),
            "ap_invoice_id": str(ap_invoice_id),
            "applied_amount": str(amount),
            "status": new_status,
        }
