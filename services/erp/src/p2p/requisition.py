"""Purchase Requisition service for P2P (P2P-1).

A PR is an internal request for goods that precedes a PO. PRs are
created from MRP's `PURCHASE_REQUISITION_CREATED` event in W2; W1
ships manual creation + a stub consumer for the event.

Status: draft → approved → converted (to PO) → rejected
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow

logger = logging.getLogger(__name__)


STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CONVERTED = "converted"


@dataclass(slots=True)
class RequisitionLineInput:
    product_id: UUID
    sku: str
    description: str | None
    quantity: Decimal
    unit: str = "each"
    estimated_unit_price: Decimal = Decimal("0")
    currency: str = "USD"
    supplier_id: UUID | None = None
    needed_by: Any | None = None


@dataclass(slots=True)
class CreateRequisitionInput:
    requester_id: UUID
    justification: str | None = None
    lines: list[RequisitionLineInput] = field(default_factory=list)


@dataclass(slots=True)
class CreateRequisitionResult:
    requisition_id: UUID
    requisition_number: str
    line_count: int
    total_estimated: Decimal


class RequisitionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, tenant_id: UUID, input: CreateRequisitionInput
    ) -> CreateRequisitionResult:
        if not input.lines:
            raise ValueError("at least one line is required")
        rid = uuid4()
        rnumber = f"PR-{rid.hex[:8].upper()}"
        total = Decimal("0")
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO purchase_requisitions (
                    id, tenant_id, requisition_number, requester_id,
                    status, justification
                ) VALUES (
                    :id, :tenant_id, :rn, :requester_id,
                    'draft', :justification
                )
                """
            ),
            {
                "id": rid,
                "tenant_id": tenant_id,
                "rn": rnumber,
                "requester_id": input.requester_id,
                "justification": input.justification,
            },
        )
        for idx, line in enumerate(input.lines, start=1):
            total += line.quantity * line.estimated_unit_price
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO purchase_requisition_lines (
                        tenant_id, requisition_id, line_number, product_id, sku,
                        description, quantity, unit, estimated_unit_price,
                        currency, supplier_id, needed_by
                    ) VALUES (
                        :tenant_id, :rid, :ln, :product_id, :sku,
                        :description, :quantity, :unit, :price,
                        :currency, :supplier_id, :needed_by
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "rid": rid,
                    "ln": idx,
                    "product_id": line.product_id,
                    "sku": line.sku,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "price": line.estimated_unit_price,
                    "currency": line.currency,
                    "supplier_id": line.supplier_id,
                    "needed_by": line.needed_by,
                },
            )
        await self._session.flush()
        return CreateRequisitionResult(
            requisition_id=rid,
            requisition_number=rnumber,
            line_count=len(input.lines),
            total_estimated=total,
        )

    async def approve(
        self, *, tenant_id: UUID, requisition_id: UUID, approver_id: UUID
    ) -> dict[str, Any]:
        # RBAC enforced at the API layer.
        await self._session.execute(
            _sa_text(
                "UPDATE purchase_requisitions SET status = 'approved', "
                "approved_by = :approver, approved_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'draft'"
            ),
            {"approver": approver_id, "now": utcnow(), "id": requisition_id, "tenant_id": tenant_id},
        )
        return {"requisition_id": str(requisition_id), "status": "approved"}

    async def reject(
        self, *, tenant_id: UUID, requisition_id: UUID, approver_id: UUID, reason: str
    ) -> dict[str, Any]:
        await self._session.execute(
            _sa_text(
                "UPDATE purchase_requisitions SET status = 'rejected', "
                "approved_by = :approver, approved_at = :now, rejected_reason = :reason "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'draft'"
            ),
            {
                "approver": approver_id,
                "now": utcnow(),
                "reason": reason,
                "id": requisition_id,
                "tenant_id": tenant_id,
            },
        )
        return {"requisition_id": str(requisition_id), "status": "rejected"}
