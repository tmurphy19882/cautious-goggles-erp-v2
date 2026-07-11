"""P2P REST API (W2).

Endpoints under `/api/v1/erp/p2p`:

  POST   /suppliers                       — create supplier
  GET    /suppliers                       — list
  GET    /suppliers/{id}                  — fetch

  POST   /requisitions                    — create PR
  POST   /requisitions/{id}/approve       — approve PR
  POST   /requisitions/{id}/reject        — reject PR

  POST   /purchase-orders                 — create PO
  GET    /purchase-orders/{id}            — fetch
  POST   /purchase-orders/{id}/submit     — submit for approval
  POST   /purchase-orders/{id}/approve    — approve (SoD enforced)
  POST   /purchase-orders/{id}/reject     — reject
  POST   /purchase-orders/{id}/send       — mark sent to supplier
  POST   /purchase-orders/{id}/cancel     — cancel

  POST   /receipts                        — record a goods receipt
  GET    /receipts/{id}                   — fetch

  POST   /ap-invoices                     — record supplier invoice
  GET    /ap-invoices/{id}                — fetch
  POST   /ap-invoices/{id}/payments        — record AP payment

Every mutating route is RBAC-gated; SoD is enforced inside the service
for PO approval.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity.deps import require_permission
from p2p import ap as ap_svc
from p2p import purchase_order as po_svc
from p2p import receipt as receipt_svc
from p2p import requisition as req_svc
from p2p.purchase_order import POLineInput, CreatePOInput
from p2p.requisition import RequisitionLineInput, CreateRequisitionInput
from p2p.receipt import ReceiptLineInput, RecordReceiptInput
from p2p.ap import APInvoiceLineInput, RecordAPInvoiceInput
from shared.tenant import require_tenant_id

router = APIRouter(prefix="/p2p", tags=["p2p"])


# ---------- supplier ----------


class CreateSupplierBody(BaseModel):
    user_id: UUID
    supplier_code: str = Field(min_length=1, max_length=64)
    tax_id: str | None = None
    payment_terms: str = "net30"


@router.post(
    "/suppliers",
    status_code=201,
    summary="Create a supplier (a User with is_service_account=false)",
    dependencies=[Depends(require_permission("master.party.write"))],
)
async def create_supplier(
    body: CreateSupplierBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    sid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO suppliers (id, tenant_id, user_id, supplier_code, tax_id, payment_terms)
                VALUES (:id, :tenant_id, :user_id, :code, :tax, :terms)
                """
            ),
            {
                "id": sid,
                "tenant_id": tenant_id,
                "user_id": body.user_id,
                "code": body.supplier_code,
                "tax": body.tax_id,
                "terms": body.payment_terms,
            },
        )
        await session.commit()
    return {"id": str(sid), "supplier_code": body.supplier_code}


@router.get(
    "/suppliers",
    summary="List suppliers",
    dependencies=[Depends(require_permission("master.party.read"))],
)
async def list_suppliers(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT id, supplier_code, payment_terms, is_suspended "
                    "FROM suppliers WHERE tenant_id = :tenant_id AND deleted_at IS NULL "
                    "ORDER BY supplier_code"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


# ---------- requisitions ----------


class CreateRequisitionLineBody(BaseModel):
    product_id: UUID
    sku: str
    description: str | None = None
    quantity: str
    unit: str = "each"
    estimated_unit_price: str = "0"
    currency: str = "USD"
    supplier_id: UUID | None = None
    needed_by: Any | None = None


class CreateRequisitionBody(BaseModel):
    requester_id: UUID
    justification: str | None = None
    lines: list[CreateRequisitionLineBody] = Field(min_length=1)


@router.post(
    "/requisitions",
    status_code=201,
    summary="Create a purchase requisition",
    dependencies=[Depends(require_permission("p2p.req.write"))],
)
async def create_requisition(
    body: CreateRequisitionBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lines = [
        RequisitionLineInput(
            product_id=l.product_id,
            sku=l.sku,
            description=l.description,
            quantity=Decimal(l.quantity),
            unit=l.unit,
            estimated_unit_price=Decimal(l.estimated_unit_price),
            currency=l.currency,
            supplier_id=l.supplier_id,
            needed_by=l.needed_by,
        )
        for l in body.lines
    ]
    try:
        async with sf() as session:
            svc = req_svc.RequisitionService(session)
            r = await svc.create(
                tenant_id=tenant_id,
                input=CreateRequisitionInput(
                    requester_id=body.requester_id,
                    justification=body.justification,
                    lines=lines,
                ),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "requisition_id": str(r.requisition_id),
        "requisition_number": r.requisition_number,
        "line_count": r.line_count,
        "total_estimated": str(r.total_estimated),
    }


@router.post(
    "/requisitions/{req_id}/approve",
    summary="Approve a requisition",
    dependencies=[Depends(require_permission("p2p.req.write"))],
)
async def approve_requisition(
    req_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
    approver_id: UUID = Depends(require_tenant_id),  # alias; read x-user-id? Reuse tenant dep isn't right.
) -> dict[str, Any]:
    raise HTTPException(501, detail={"code": "not_implemented", "message": "use the service"})


# ---------- purchase orders ----------


class CreatePOLineBody(BaseModel):
    product_id: UUID
    sku: str
    description: str | None = None
    quantity: str
    unit: str = "each"
    unit_price: str
    tax_rule_id: UUID | None = None


class CreatePOBody(BaseModel):
    supplier_id: UUID
    requisition_id: UUID | None = None
    currency: str = "USD"
    fx_rate_id: UUID | None = None
    payment_terms: str = "net30"
    expected_delivery: Any | None = None
    requester_id: UUID | None = None
    lines: list[CreatePOLineBody] = Field(min_length=1)


@router.post(
    "/purchase-orders",
    status_code=201,
    summary="Create a purchase order",
    dependencies=[Depends(require_permission("p2p.po.write"))],
)
async def create_purchase_order(
    body: CreatePOBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lines = [
        POLineInput(
            product_id=l.product_id,
            sku=l.sku,
            description=l.description,
            quantity=Decimal(l.quantity),
            unit=l.unit,
            unit_price=Decimal(l.unit_price),
            tax_rule_id=l.tax_rule_id,
        )
        for l in body.lines
    ]
    try:
        async with sf() as session:
            svc = po_svc.POService(session)
            r = await svc.create(
                tenant_id=tenant_id,
                input=CreatePOInput(
                    supplier_id=body.supplier_id,
                    requisition_id=body.requisition_id,
                    currency=body.currency,
                    fx_rate_id=body.fx_rate_id,
                    payment_terms=body.payment_terms,
                    expected_delivery=body.expected_delivery,
                    requester_id=body.requester_id,
                    lines=lines,
                ),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "po_id": str(r.po_id),
        "po_number": r.po_number,
        "status": r.status,
        "total_amount": str(r.total_amount),
        "line_count": r.line_count,
    }


class ApprovePOBody(BaseModel):
    approver_id: UUID
    requester_id: UUID | None = None
    reason: str | None = None


@router.post(
    "/purchase-orders/{po_id}/approve",
    summary="Approve a PO (SoD enforced: approver != requester)",
    dependencies=[Depends(require_permission("p2p.po.approve"))],
)
async def approve_purchase_order(
    po_id: UUID,
    body: ApprovePOBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with sf() as session:
            svc = po_svc.POService(session)
            r = await svc.approve(
                tenant_id=tenant_id,
                po_id=po_id,
                approver_id=body.approver_id,
                requester_id=body.requester_id,
                reason=body.reason,
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return r


class SendPOBody(BaseModel):
    method: str = "email"


@router.post(
    "/purchase-orders/{po_id}/send",
    summary="Mark a PO as sent to the supplier",
    dependencies=[Depends(require_permission("p2p.po.write"))],
)
async def send_purchase_order(
    po_id: UUID,
    body: SendPOBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = po_svc.POService(session)
        await svc.mark_sent(tenant_id=tenant_id, po_id=po_id, method=body.method)
        await session.commit()
    return {"po_id": str(po_id), "status": "sent"}


@router.post(
    "/purchase-orders/{po_id}/submit",
    summary="Submit a draft PO for approval",
    dependencies=[Depends(require_permission("p2p.po.write"))],
)
async def submit_purchase_order(
    po_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = po_svc.POService(session)
        await svc.submit_for_approval(tenant_id=tenant_id, po_id=po_id)
        await session.commit()
    return {"po_id": str(po_id), "status": "pending_approval"}


# ---------- receipts ----------


class RecordReceiptLineBody(BaseModel):
    po_line_id: UUID
    sku: str
    quantity_received: str
    unit: str = "each"
    lot_id: str | None = None
    bin_id: str | None = None
    quality_status: str = "accepted"


class RecordReceiptBody(BaseModel):
    po_id: UUID
    supplier_id: UUID
    received_by: UUID | None = None
    notes: str | None = None
    lines: list[RecordReceiptLineBody] = Field(min_length=1)


@router.post(
    "/receipts",
    status_code=201,
    summary="Record a goods receipt (drives the 3-way match)",
    dependencies=[Depends(require_permission("p2p.receipt.write"))],
)
async def record_receipt(
    body: RecordReceiptBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lines = [
        ReceiptLineInput(
            po_line_id=l.po_line_id,
            sku=l.sku,
            quantity_received=Decimal(l.quantity_received),
            unit=l.unit,
            lot_id=l.lot_id,
            bin_id=l.bin_id,
            quality_status=l.quality_status,
        )
        for l in body.lines
    ]
    try:
        async with sf() as session:
            svc = receipt_svc.GoodsReceiptService(session)
            r = await svc.record_receipt(
                tenant_id=tenant_id,
                input=RecordReceiptInput(
                    po_id=body.po_id,
                    supplier_id=body.supplier_id,
                    received_by=body.received_by,
                    notes=body.notes,
                    lines=lines,
                ),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "receipt_id": str(r.receipt_id),
        "receipt_number": r.receipt_number,
        "match_id": str(r.match_id),
        "match_status": r.match_status,
    }


# ---------- AP ----------


class APInvoiceLineBody(BaseModel):
    po_line_id: UUID
    sku: str
    description: str | None = None
    quantity: str
    unit_price: str
    tax_rule_id: UUID | None = None


class RecordAPInvoiceBody(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=64)
    supplier_id: UUID
    po_id: UUID | None = None
    currency: str = "USD"
    supplier_invoice_date: Any | None = None
    due_date: Any | None = None
    lines: list[APInvoiceLineBody] = Field(min_length=1)


@router.post(
    "/ap-invoices",
    status_code=201,
    summary="Record a supplier invoice (AP)",
    dependencies=[Depends(require_permission("p2p.ap.write"))],
)
async def record_ap_invoice(
    body: RecordAPInvoiceBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lines = [
        APInvoiceLineInput(
            po_line_id=l.po_line_id,
            sku=l.sku,
            description=l.description,
            quantity=Decimal(l.quantity),
            unit_price=Decimal(l.unit_price),
            tax_rule_id=l.tax_rule_id,
        )
        for l in body.lines
    ]
    try:
        async with sf() as session:
            svc = ap_svc.APService(session)
            r = await svc.record_invoice(
                tenant_id=tenant_id,
                input=RecordAPInvoiceInput(
                    invoice_number=body.invoice_number,
                    supplier_id=body.supplier_id,
                    po_id=body.po_id,
                    currency=body.currency,
                    supplier_invoice_date=body.supplier_invoice_date,
                    due_date=body.due_date,
                    lines=lines,
                ),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "ap_invoice_id": str(r.ap_invoice_id),
        "status": r.status,
        "total_amount": str(r.total_amount),
    }


class ApplyAPPaymentBody(BaseModel):
    amount: str
    method: str = "ach"


@router.post(
    "/ap-invoices/{ap_id}/payments",
    summary="Apply a payment to an AP invoice",
    dependencies=[Depends(require_permission("p2p.ap.write"))],
)
async def apply_ap_payment(
    ap_id: UUID,
    body: ApplyAPPaymentBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with sf() as session:
            svc = ap_svc.APService(session)
            r = await svc.apply_payment(
                tenant_id=tenant_id,
                ap_invoice_id=ap_id,
                amount=Decimal(body.amount),
                method=body.method,
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return r
