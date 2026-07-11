"""FastAPI router for the O2C service (W1).

Endpoints:

  POST   /api/v1/erp/sales-orders                      — create (multiline, O2C-1)
  POST   /api/v1/erp/sales-orders/{id}/confirm         — confirm + publish ORDER_CONFIRMED
  POST   /api/v1/erp/sales-orders/{id}/cancel          — cancel + release reservation
  GET    /api/v1/erp/sales-orders/{id}                 — fetch
  GET    /api/v1/erp/sales-orders                      — list
  POST   /api/v1/erp/sales-orders/{id}/payments        — record + apply payment
  POST   /api/v1/erp/sales-orders/{id}/run-workflow     — sync run of the O2C workflow

The workflow endpoint is the W1 smoke path: it creates a SO (if
needed), confirms it, and generates the invoice in one request. W1.1
replaces the synchronous invoice step with a SHIP_CONFIRMED
consumer-driven flow.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity.deps import require_permission
from o2c import invoice as invoice_svc
from o2c.sales_order import (
    CreateSalesOrderInput,
    CreateSalesOrderResult,
    OrderError,
    OrderNotFoundError,
    SalesOrderLineInput,
    SalesOrderService,
    ConfirmSalesOrderResult,
)
from o2c.workflows import OrderToCashWorkflow
from shared.errors import NotFoundError
from shared.tenant import require_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sales-orders", tags=["o2c"])


# ---------- request / response schemas ----------


class CreateOrderLineBody(BaseModel):
    product_id: UUID
    sku: str = Field(min_length=1, max_length=64)
    description: str | None = None
    quantity: str = Field(description="Decimal as string")
    unit: str = "each"
    unit_price: str | None = None
    discount_pct: str = "0"
    tax_rule_id: UUID | None = None
    ship_from_location_id: UUID | None = None


class CreateOrderBody(BaseModel):
    customer_id: UUID
    currency: str = "USD"
    fx_rate_id: UUID | None = None
    notes: str | None = None
    lines: list[CreateOrderLineBody] = Field(min_length=1)


class CreateOrderResponse(BaseModel):
    order_id: UUID
    order_number: str
    status: str
    currency: str
    total_amount: str
    line_count: int


class ConfirmOrderResponse(BaseModel):
    order_id: UUID
    order_number: str
    status: str
    event_id: UUID
    line_count: int
    total_amount: str


class CancelOrderBody(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class ApplyPaymentBody(BaseModel):
    invoice_id: UUID
    amount: str | None = None


# ---------- helpers ----------


def _get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _parse_decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _to_result_dict(result: CreateSalesOrderResult) -> CreateOrderResponse:
    return CreateOrderResponse(
        order_id=result.order_id,
        order_number=result.order_number,
        status=result.status,
        currency=result.currency,
        total_amount=str(result.total_amount),
        line_count=result.line_count,
    )


def _to_confirm_dict(result: ConfirmSalesOrderResult) -> ConfirmOrderResponse:
    return ConfirmOrderResponse(
        order_id=result.order_id,
        order_number=result.order_number,
        status=result.status,
        event_id=result.event_id,
        line_count=result.line_count,
        total_amount=str(result.total_amount),
    )


# ---------- endpoints ----------


@router.post(
    "",
    response_model=CreateOrderResponse,
    status_code=201,
    summary="Create a sales order (multiline)",
    dependencies=[Depends(require_permission("o2c.so.write"))],
)
async def create_sales_order(
    body: CreateOrderBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> CreateOrderResponse:
    sf = _get_session_factory(request)
    lines = [
        SalesOrderLineInput(
            product_id=line.product_id,
            sku=line.sku,
            description=line.description,
            quantity=Decimal(line.quantity),
            unit=line.unit,
            unit_price=_parse_decimal(line.unit_price),
            discount_pct=Decimal(line.discount_pct),
            tax_rule_id=line.tax_rule_id,
            ship_from_location_id=line.ship_from_location_id,
        )
        for line in body.lines
    ]
    input_ = CreateSalesOrderInput(
        customer_id=body.customer_id,
        currency=body.currency,
        fx_rate_id=body.fx_rate_id,
        notes=body.notes,
        lines=lines,
    )
    try:
        async with sf() as session:
            svc = SalesOrderService(session)
            result = await svc.create(tenant_id=tenant_id, input=input_)
            await session.commit()
    except OrderError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)})
    return _to_result_dict(result)


@router.post(
    "/{order_id}/confirm",
    response_model=ConfirmOrderResponse,
    summary="Confirm a sales order (credit check, reservation, publish ORDER_CONFIRMED)",
    dependencies=[Depends(require_permission("o2c.so.confirm"))],
)
async def confirm_sales_order(
    order_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> ConfirmOrderResponse:
    sf = _get_session_factory(request)
    try:
        async with sf() as session:
            svc = SalesOrderService(session)
            result = await svc.confirm(
                tenant_id=tenant_id, order_id=order_id,
                trace_id=request.headers.get("traceparent"),
            )
            await session.commit()
    except OrderError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)})
    return _to_confirm_dict(result)


@router.post(
    "/{order_id}/cancel",
    summary="Cancel a sales order (releases reservation, releases credit hold)",
    dependencies=[Depends(require_permission("o2c.so.cancel"))],
)
async def cancel_sales_order(
    order_id: UUID,
    body: CancelOrderBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf = _get_session_factory(request)
    try:
        async with sf() as session:
            svc = SalesOrderService(session)
            result = await svc.cancel(tenant_id=tenant_id, order_id=order_id, reason=body.reason)
            await session.commit()
    except OrderError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)})
    return result


@router.get(
    "/{order_id}",
    summary="Fetch a sales order",
    dependencies=[Depends(require_permission("o2c.so.read"))],
)
async def get_sales_order(
    order_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf = _get_session_factory(request)
    async with sf() as session:
        order = (
            await session.execute(
                _sa_text(
                    "SELECT * FROM sales_orders WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": order_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if order is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "not found"})
        lines = (
            await session.execute(
                _sa_text(
                    "SELECT * FROM sales_order_lines WHERE sales_order_id = :id "
                    "AND tenant_id = :tenant_id ORDER BY line_number"
                ),
                {"id": order_id, "tenant_id": tenant_id},
            )
        ).mappings().all()
    return {
        "id": str(order["id"]),
        "tenant_id": str(order["tenant_id"]),
        "order_number": order["order_number"],
        "customer_id": str(order["customer_id"]),
        "status": order["status"],
        "currency": order["currency"],
        "subtotal": str(order["subtotal"]),
        "tax_amount": str(order["tax_amount"]),
        "total_amount": str(order["total_amount"]),
        "cogs_amount": str(order["cogs_amount"]),
        "invoice_id": str(order["invoice_id"]) if order["invoice_id"] else None,
        "notes": order["notes"],
        "lines": [
            {
                "id": str(l["id"]),
                "line_number": l["line_number"],
                "product_id": str(l["product_id"]),
                "sku": l["sku"],
                "quantity": str(l["quantity"]),
                "unit": l["unit"],
                "unit_price": str(l["unit_price"]),
                "discount_pct": str(l["discount_pct"]),
                "tax_amount": str(l["tax_amount"]),
                "line_total": str(l["line_total"]),
                "cogs_unit_cost": str(l["cogs_unit_cost"]) if l["cogs_unit_cost"] else None,
                "cogs_total": str(l["cogs_total"]) if l["cogs_total"] else None,
            }
            for l in lines
        ],
    }


@router.get(
    "",
    summary="List sales orders",
    dependencies=[Depends(require_permission("o2c.so.read"))],
)
async def list_sales_orders(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    sf = _get_session_factory(request)
    limit = max(1, min(500, limit))
    async with sf() as session:
        params: dict[str, Any] = {"tenant_id": tenant_id, "limit": limit}
        where = "WHERE tenant_id = :tenant_id"
        if status:
            where += " AND status = :status"
            params["status"] = status
        rows = (
            await session.execute(
                _sa_text(
                    f"SELECT id, order_number, customer_id, status, currency, "
                    f"total_amount, created_at FROM sales_orders {where} "
                    f"ORDER BY created_at DESC LIMIT :limit"
                ),
                params,
            )
        ).mappings().all()
    return {
        "items": [
            {
                "id": str(r["id"]),
                "order_number": r["order_number"],
                "customer_id": str(r["customer_id"]),
                "status": r["status"],
                "currency": r["currency"],
                "total_amount": str(r["total_amount"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }


@router.post(
    "/{order_id}/payments",
    summary="Record a payment and apply it to the order's invoice (FIFO if multiple)",
    dependencies=[Depends(require_permission("o2c.payment.write"))],
)
async def record_payment(
    order_id: UUID,
    request: Request,
    body: ApplyPaymentBody,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    """Record a payment and apply it to the SO's invoice.

    The W1 endpoint takes a pre-existing invoice; for W1 the
    payment is associated with the SO. (W1.1 adds a free-standing
    `POST /payments` that can be applied to multiple invoices.)
    """
    sf = _get_session_factory(request)
    async with sf() as session:
        inv = (
            await session.execute(
                _sa_text(
                    "SELECT id, customer_id FROM invoices "
                    "WHERE id = :id AND sales_order_id = :so_id AND tenant_id = :tenant_id"
                ),
                {"id": body.invoice_id, "so_id": order_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if inv is None:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "invoice not found for order"}
            )
        # Create the payment row.
        from decimal import Decimal as _Dec
        amount = _Dec(body.amount) if body.amount else None
        from uuid import uuid4
        pay_id = uuid4()
        await session.execute(
            _sa_text(
                """
                INSERT INTO payments (
                    id, tenant_id, customer_id, currency, amount,
                    method, reference, unapplied_amount
                ) VALUES (
                    :id, :tenant_id, :customer_id, 'USD', :amount,
                    'wire', NULL, :amount
                )
                """
            ),
            {
                "id": pay_id,
                "tenant_id": tenant_id,
                "customer_id": inv["customer_id"],
                "amount": amount or _Dec("0"),
            },
        )
        result = await invoice_svc.apply_payment(
            session,
            tenant_id=tenant_id,
            payment_id=pay_id,
            target_invoice_id=body.invoice_id,
            amount=amount,
            trace_id=request.headers.get("traceparent"),
        )
        await session.commit()
    return {
        "payment_id": str(result.payment_id),
        "applied_amount": str(result.applied_amount),
        "unapplied_amount": str(result.unapplied_amount),
        "invoices_affected": [str(i) for i in result.invoices_affected],
        "event_id": str(result.event_id) if result.event_id else None,
    }


@router.post(
    "/{order_id}/run-workflow",
    summary="Synchronously run the OrderToCash workflow (W1 smoke path)",
    dependencies=[Depends(require_permission("o2c.so.confirm"))],
)
async def run_workflow(
    order_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf = _get_session_factory(request)
    workflow = OrderToCashWorkflow(sf)
    result = await workflow.run(
        tenant_id=tenant_id,
        sales_order_id=order_id,
        trace_id=request.headers.get("traceparent"),
    )
    return {
        "sales_order_id": str(result.sales_order_id),
        "final_status": result.final_status,
        "invoice_id": str(result.invoice_id) if result.invoice_id else None,
        "events_published": result.events_published,
        "error": result.error,
    }
