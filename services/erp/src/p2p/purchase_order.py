"""Purchase Order service for P2P (P2P-2, P2P-3).

A PO is the supplier-facing counterpart of the SO. Status:

    draft → pending_approval → approved → sent → partially_received
                                                          → received
                                                          → closed
                                ↘ rejected
                                ↘ cancelled

Approval enforces SoD (P2P-3): the approver must not be the requester.
The check lives in the service; the API gates on `p2p.po.approve`
and the `approve()` method raises if approver == requester.
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
from shared.events import (
    TOPIC_PO_APPROVED,
    build_envelope,
)
from shared.outbox import OutboxStore
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


STATUS_DRAFT = "draft"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SENT = "sent"
STATUS_PARTIALLY_RECEIVED = "partially_received"
STATUS_RECEIVED = "received"
STATUS_CLOSED = "closed"
STATUS_CANCELLED = "cancelled"


@dataclass(slots=True)
class POLineInput:
    product_id: UUID
    sku: str
    description: str | None
    quantity: Decimal
    unit: str = "each"
    unit_price: Decimal = Decimal("0")
    tax_rule_id: UUID | None = None


@dataclass(slots=True)
class CreatePOInput:
    supplier_id: UUID
    requisition_id: UUID | None = None
    currency: str = "USD"
    fx_rate_id: UUID | None = None
    payment_terms: str = "net30"
    expected_delivery: Any | None = None
    requester_id: UUID | None = None  # for SoD
    lines: list[POLineInput] = field(default_factory=list)


@dataclass(slots=True)
class CreatePOResult:
    po_id: UUID
    po_number: str
    status: str
    total_amount: Decimal
    line_count: int


class POService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = OutboxStore(session)

    async def create(
        self, *, tenant_id: UUID, input: CreatePOInput
    ) -> CreatePOResult:
        if not input.lines:
            raise ValueError("at least one line is required")
        # Check supplier is not suspended.
        sup = (
            await self._session.execute(
                _sa_text(
                    "SELECT is_suspended, suspended_reason FROM suppliers "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": input.supplier_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if sup is None:
            raise ValueError(f"supplier not found: {input.supplier_id}")
        if sup["is_suspended"]:
            # P2P-8: SUPPLIER_SUSPENDED is a hard block.
            raise ValueError(
                f"supplier is suspended: {sup['suspended_reason'] or 'no reason given'}"
            )

        pid = uuid4()
        pnumber = f"PO-{pid.hex[:8].upper()}"

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
                    "product_id": line.product_id,
                    "sku": line.sku,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "unit_price": line.unit_price,
                    "tax_rule_id": line.tax_rule_id,
                    "tax_amount": line_tax,
                    "line_total": line_total,
                }
            )
        total = subtotal + tax_amount

        await self._session.execute(
            _sa_text(
                """
                INSERT INTO purchase_orders (
                    id, tenant_id, po_number, requisition_id, supplier_id,
                    status, currency, fx_rate_id, subtotal, tax_amount,
                    total_amount, payment_terms, expected_delivery
                ) VALUES (
                    :id, :tenant_id, :pn, :req_id, :supplier_id,
                    'draft', :currency, :fx, :subtotal, :tax,
                    :total, :terms, :delivery
                )
                """
            ),
            {
                "id": pid,
                "tenant_id": tenant_id,
                "pn": pnumber,
                "req_id": input.requisition_id,
                "supplier_id": input.supplier_id,
                "currency": input.currency,
                "fx": input.fx_rate_id,
                "subtotal": subtotal,
                "tax": tax_amount,
                "total": total,
                "terms": input.payment_terms,
                "delivery": input.expected_delivery,
            },
        )
        for row in line_rows:
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO purchase_order_lines (
                        tenant_id, po_id, line_number, product_id, sku,
                        description, quantity, unit, unit_price,
                        tax_rule_id, tax_amount, line_total
                    ) VALUES (
                        :tenant_id, :pid, :ln, :product_id, :sku,
                        :description, :quantity, :unit, :unit_price,
                        :tax_rule_id, :tax_amount, :line_total
                    )
                    """
                ),
                {"tenant_id": tenant_id, "pid": pid, **row},
            )
        # If a PR is linked, mark it converted.
        if input.requisition_id is not None:
            await self._session.execute(
                _sa_text(
                    "UPDATE purchase_requisitions SET status = 'converted', "
                    "converted_po_id = :pid, updated_at = :now "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"pid": pid, "now": utcnow(), "id": input.requisition_id, "tenant_id": tenant_id},
            )
        await self._session.flush()
        return CreatePOResult(
            po_id=pid,
            po_number=pnumber,
            status=STATUS_DRAFT,
            total_amount=total,
            line_count=len(line_rows),
        )

    async def submit_for_approval(self, *, tenant_id: UUID, po_id: UUID) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE purchase_orders SET status = 'pending_approval', updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'draft'"
            ),
            {"now": utcnow(), "id": po_id, "tenant_id": tenant_id},
        )

    async def approve(
        self,
        *,
        tenant_id: UUID,
        po_id: UUID,
        approver_id: UUID,
        requester_id: UUID | None = None,
        reason: str | None = None,
        threshold_amount: Decimal | None = None,
    ) -> dict[str, Any]:
        """Approve a PO. Enforces SoD (P2P-3): approver ≠ requester.

        Emits `PO_APPROVED` on the outbox so WMS can pre-stage the ASN
        and Compliance can audit the approval.
        """
        if requester_id is not None and approver_id == requester_id:
            raise ValueError("approver must not be the requester (SoD violation)")
        # Verify the PO is in a state that can be approved.
        po = (
            await self._session.execute(
                _sa_text(
                    "SELECT * FROM purchase_orders WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": po_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if po is None:
            raise ValueError(f"purchase order not found: {po_id}")
        if po["status"] not in (STATUS_DRAFT, STATUS_PENDING_APPROVAL):
            raise ValueError(f"cannot approve PO in status {po['status']}")

        await self._session.execute(
            _sa_text(
                """
                UPDATE purchase_orders SET status = 'approved',
                approved_by = :approver, approved_at = :now, updated_at = :now
                WHERE id = :id
                """
            ),
            {"approver": approver_id, "now": utcnow(), "id": po_id},
        )
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO po_approvals (
                    tenant_id, po_id, approver_id, decision, threshold_amount, reason
                ) VALUES (:tenant_id, :pid, :approver, 'approved', :threshold, :reason)
                """
            ),
            {
                "tenant_id": tenant_id,
                "pid": po_id,
                "approver": approver_id,
                "threshold": threshold_amount or Decimal(str(po["total_amount"])),
                "reason": reason,
            },
        )
        # P2P-7: emit PO_APPROVED.
        envelope = build_envelope(
            event_type=TOPIC_PO_APPROVED,
            tenant_id=tenant_id,
            trace_id="n/a",
            payload={
                "po_id": str(po_id),
                "po_number": po["po_number"],
                "supplier_id": str(po["supplier_id"]),
                "currency": po["currency"],
                "total_amount": str(po["total_amount"]),
                "approver_id": str(approver_id),
            },
        )
        await self._outbox.emit(
            tenant_id=tenant_id,
            aggregate_type="purchase_order",
            aggregate_id=po_id,
            envelope=envelope,
            topic=TOPIC_PO_APPROVED,
        )
        return {"po_id": str(po_id), "status": "approved"}

    async def reject(
        self, *, tenant_id: UUID, po_id: UUID, approver_id: UUID, reason: str
    ) -> dict[str, Any]:
        await self._session.execute(
            _sa_text(
                """
                UPDATE purchase_orders SET status = 'rejected',
                approved_by = :approver, approved_at = :now, rejected_reason = :reason,
                updated_at = :now
                WHERE id = :id AND tenant_id = :tenant_id
                AND status IN ('draft', 'pending_approval')
                """
            ),
            {
                "approver": approver_id,
                "now": utcnow(),
                "reason": reason,
                "id": po_id,
                "tenant_id": tenant_id,
            },
        )
        return {"po_id": str(po_id), "status": "rejected"}

    async def mark_sent(
        self, *, tenant_id: UUID, po_id: UUID, method: str = "email"
    ) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE purchase_orders SET status = 'sent', sent_at = :now, "
                "sent_method = :method, updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'approved'"
            ),
            {"now": utcnow(), "method": method, "id": po_id, "tenant_id": tenant_id},
        )

    async def cancel(
        self, *, tenant_id: UUID, po_id: UUID, reason: str
    ) -> dict[str, Any]:
        await self._session.execute(
            _sa_text(
                "UPDATE purchase_orders SET status = 'cancelled', "
                "rejected_reason = :reason, updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id "
                "AND status NOT IN ('received', 'closed', 'cancelled')"
            ),
            {"now": utcnow(), "reason": reason, "id": po_id, "tenant_id": tenant_id},
        )
        return {"po_id": str(po_id), "status": "cancelled"}
