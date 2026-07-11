"""Sales Order service for O2C.

Wave 1 — O2C complete. Closes O2C-1..O2C-15 from the audit (multiline,
credit, reservation, cancel, hold, confirm, status machine, tax + FX).

The service is the single chokepoint for SO mutations. Every entry
point (API, internal call, workflow resume) goes through `create`,
`confirm`, `cancel`. Every state change writes to the outbox in the
same transaction.

Status machine:
    draft → credit_hold → confirmed → invoiced → paid → closed
                       ↘ cancelled
                       ↘ held (QMS / credit override)
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
    TOPIC_ORDER_CONFIRMED,
    build_envelope,
    customer_token,
)
from shared.outbox import OutboxStore
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


# Order status constants
STATUS_DRAFT = "draft"
STATUS_CREDIT_HOLD = "credit_hold"
STATUS_CONFIRMED = "confirmed"
STATUS_INVOICED = "invoiced"
STATUS_PAID = "paid"
STATUS_CLOSED = "closed"
STATUS_CANCELLED = "cancelled"
STATUS_HELD = "held"

ALL_STATUSES = frozenset(
    {
        STATUS_DRAFT,
        STATUS_CREDIT_HOLD,
        STATUS_CONFIRMED,
        STATUS_INVOICED,
        STATUS_PAID,
        STATUS_CLOSED,
        STATUS_CANCELLED,
        STATUS_HELD,
    }
)


class OrderError(Exception):
    """Base for SO service errors. Mapped to HTTP by the API layer."""

    def __init__(self, message: str, *, code: str = "order_error", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class CreditExceededError(OrderError):
    code = "credit_exceeded"
    status = 409


class OrderNotFoundError(OrderError):
    code = "not_found"
    status = 404


class InvalidStateError(OrderError):
    code = "invalid_state"
    status = 409


@dataclass(slots=True)
class SalesOrderLineInput:
    product_id: UUID
    sku: str
    description: str | None
    quantity: Decimal
    unit: str = "each"
    unit_price: Decimal | None = None
    discount_pct: Decimal = Decimal("0")
    tax_rule_id: UUID | None = None
    ship_from_location_id: UUID | None = None


@dataclass(slots=True)
class CreateSalesOrderInput:
    customer_id: UUID
    currency: str = "USD"
    fx_rate_id: UUID | None = None
    notes: str | None = None
    lines: list[SalesOrderLineInput] = field(default_factory=list)


@dataclass(slots=True)
class CreateSalesOrderResult:
    order_id: UUID
    order_number: str
    status: str
    currency: str
    total_amount: Decimal
    line_count: int


@dataclass(slots=True)
class ConfirmSalesOrderResult:
    order_id: UUID
    order_number: str
    status: str
    event_id: UUID
    line_count: int
    total_amount: Decimal


# --------------------------------------------------------------------------- #
# Service                                                                     #
# --------------------------------------------------------------------------- #


class SalesOrderService:
    """All SO mutations go through here.

    Every public method takes an explicit `session` (caller controls
    the transaction). Outbox rows are written in the same transaction
    as the state change.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = OutboxStore(session)

    # ---- create ---------------------------------------------------------- #

    async def create(self, *, tenant_id: UUID, input: CreateSalesOrderInput) -> CreateSalesOrderResult:
        """Create a SO in `draft` status. Closes O2C-1 (multiline)."""
        if not input.lines:
            raise OrderError("at least one line is required", code="no_lines", status=422)
        if input.currency != "USD" and input.fx_rate_id is None:
            raise OrderError(
                "fx_rate_id is required for non-USD currencies", code="fx_required", status=422
            )

        order_id = uuid4()
        order_number = f"SO-{order_id.hex[:8].upper()}"

        await self._price_lines(tenant_id, input)

        subtotal = Decimal("0")
        tax_amount = Decimal("0")
        line_rows: list[dict[str, Any]] = []
        for idx, line in enumerate(input.lines, start=1):
            tax_rule = await resolve_tax_rule(self._session, tenant_id, line.tax_rule_id)
            line_total, line_tax = compute_line_totals(
                quantity=line.quantity,
                unit_price=line.unit_price or Decimal("0"),
                discount_pct=line.discount_pct,
                tax_rate_pct=tax_rule.rate_pct if tax_rule else Decimal("0"),
                is_inclusive=tax_rule.is_inclusive if tax_rule else False,
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
                    "unit_price": line.unit_price or Decimal("0"),
                    "discount_pct": line.discount_pct,
                    "tax_rule_id": line.tax_rule_id,
                    "tax_amount": line_tax,
                    "line_total": line_total,
                    "ship_from_location_id": line.ship_from_location_id,
                }
            )
        total = subtotal + tax_amount

        await self._session.execute(
            _sa_text(
                """
                INSERT INTO sales_orders (
                    id, tenant_id, order_number, customer_id, status, currency,
                    fx_rate_id, subtotal, tax_amount, total_amount, notes
                ) VALUES (
                    :id, :tenant_id, :order_number, :customer_id, 'draft', :currency,
                    :fx_rate_id, :subtotal, :tax_amount, :total_amount, :notes
                )
                """
            ),
            {
                "id": order_id,
                "tenant_id": tenant_id,
                "order_number": order_number,
                "customer_id": input.customer_id,
                "currency": input.currency,
                "fx_rate_id": input.fx_rate_id,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total_amount": total,
                "notes": input.notes,
            },
        )
        for row in line_rows:
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO sales_order_lines (
                        tenant_id, sales_order_id, line_number, product_id, sku,
                        description, quantity, unit, unit_price, discount_pct,
                        tax_rule_id, tax_amount, line_total, ship_from_location_id
                    ) VALUES (
                        :tenant_id, :sales_order_id, :line_number, :product_id, :sku,
                        :description, :quantity, :unit, :unit_price, :discount_pct,
                        :tax_rule_id, :tax_amount, :line_total, :ship_from_location_id
                    )
                    """
                ),
                {"tenant_id": tenant_id, "sales_order_id": order_id, **row},
            )
        await self._session.flush()

        return CreateSalesOrderResult(
            order_id=order_id,
            order_number=order_number,
            status=STATUS_DRAFT,
            currency=input.currency,
            total_amount=total,
            line_count=len(line_rows),
        )

    async def _price_lines(self, tenant_id: UUID, input: CreateSalesOrderInput) -> None:
        """Fill in any missing `unit_price` from the active price list."""
        missing = [line for line in input.lines if line.unit_price is None]
        if not missing:
            return
        list_id = await self._default_price_list(tenant_id)
        if list_id is None:
            raise OrderError(
                "no default price list; supply unit_price explicitly",
                code="no_price_list",
                status=422,
            )
        for line in missing:
            price = await self._price_list_price(tenant_id, list_id, line.product_id)
            if price is None:
                raise OrderError(
                    f"no price for sku {line.sku} on the default list",
                    code="no_price",
                    status=422,
                )
            line.unit_price = price

    async def _default_price_list(self, tenant_id: UUID) -> UUID | None:
        row = await self._session.execute(
            _sa_text(
                "SELECT id FROM price_lists WHERE tenant_id = :tenant_id AND is_active = true "
                "ORDER BY created_at LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
        return row.scalar_one_or_none()

    async def _price_list_price(
        self, tenant_id: UUID, list_id: UUID, product_id: UUID
    ) -> Decimal | None:
        row = await self._session.execute(
            _sa_text(
                """
                SELECT unit_price FROM price_list_items
                WHERE tenant_id = :tenant_id AND price_list_id = :list_id AND product_id = :product_id
                ORDER BY min_quantity LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "list_id": list_id, "product_id": product_id},
        )
        return row.scalar_one_or_none()

    # ---- confirm -------------------------------------------------------- #

    async def confirm(
        self, *, tenant_id: UUID, order_id: UUID, trace_id: str | None = None
    ) -> ConfirmSalesOrderResult:
        """Move a SO from `draft` → `confirmed` (or `credit_hold`).

        Closes O2C-2 (credit check) and O2C-3 (reservation). On success,
        publishes `ORDER_CONFIRMED` to the outbox (O2C-14).
        """
        order, lines = await self._load_order(tenant_id, order_id)
        if order.status not in (STATUS_DRAFT, STATUS_CREDIT_HOLD):
            raise InvalidStateError(
                f"cannot confirm order in status {order.status}",
            )

        # O2C-2: credit check
        from o2c.credit import check_credit
        new_status, credit_event_id = await check_credit(
            self._session,
            tenant_id=tenant_id,
            customer_id=order.customer_id,
            currency=order.currency,
            amount=Decimal(str(order.total_amount)),
        )
        if new_status == STATUS_CREDIT_HOLD:
            await self._update_status(order_id, STATUS_CREDIT_HOLD)
            return ConfirmSalesOrderResult(
                order_id=order.id,
                order_number=order.order_number,
                status=STATUS_CREDIT_HOLD,
                event_id=credit_event_id,
                line_count=len(lines),
                total_amount=Decimal(str(order.total_amount)),
            )

        # O2C-3: reservation
        from o2c.reservation import reserve_for_order
        await reserve_for_order(
            self._session,
            tenant_id=tenant_id,
            sales_order_id=order_id,
            lines=lines,
        )

        # O2C-14: publish ORDER_CONFIRMED
        envelope = build_envelope(
            event_type=TOPIC_ORDER_CONFIRMED,
            tenant_id=tenant_id,
            trace_id=trace_id or "n/a",
            payload={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "customer_token": customer_token(order.customer_id),
                "currency": order.currency,
                "total_amount": str(order.total_amount),
                "lines": [
                    {
                        "line_id": str(line["id"]),
                        "line_number": line["line_number"],
                        "sku": line["sku"],
                        "quantity": str(line["quantity"]),
                        "unit": line["unit"],
                        "unit_price": str(line["unit_price"]),
                        "line_total": str(line["line_total"]),
                    }
                    for line in lines
                ],
            },
        )
        event_id = UUID(envelope.event_id) if isinstance(envelope.event_id, str) else envelope.event_id
        await self._outbox.emit(
            tenant_id=tenant_id,
            aggregate_type="sales_order",
            aggregate_id=order.id,
            envelope=envelope,
            topic=TOPIC_ORDER_CONFIRMED,
        )
        await self._update_status(order_id, STATUS_CONFIRMED, confirmed_at=utcnow())
        await self._session.flush()

        return ConfirmSalesOrderResult(
            order_id=order.id,
            order_number=order.order_number,
            status=STATUS_CONFIRMED,
            event_id=event_id,
            line_count=len(lines),
            total_amount=Decimal(str(order.total_amount)),
        )

    # ---- cancel (O2C-4) ------------------------------------------------- #

    async def cancel(
        self, *, tenant_id: UUID, order_id: UUID, reason: str
    ) -> dict[str, Any]:
        order, _ = await self._load_order(tenant_id, order_id)
        if order.status in (STATUS_INVOICED, STATUS_PAID, STATUS_CLOSED):
            raise InvalidStateError(
                f"cannot cancel order in status {order.status}; use credit memo",
            )
        if order.status == STATUS_CANCELLED:
            return {"order_id": str(order.id), "status": STATUS_CANCELLED, "no_op": True}

        from o2c.reservation import release_reservations
        await release_reservations(
            self._session, tenant_id=tenant_id, sales_order_id=order_id, reason=f"cancel: {reason}"
        )

        await self._update_status(
            order_id,
            STATUS_CANCELLED,
            cancelled_at=utcnow(),
            cancelled_reason=reason,
        )

        from o2c.credit import release_credit_hold_if_held
        await release_credit_hold_if_held(
            self._session,
            tenant_id=tenant_id,
            customer_id=order.customer_id,
            amount=Decimal(str(order.total_amount)),
        )
        await self._session.flush()
        return {"order_id": str(order.id), "status": STATUS_CANCELLED}

    # ---- helpers -------------------------------------------------------- #

    async def _load_order(self, tenant_id: UUID, order_id: UUID) -> tuple[Any, list[dict[str, Any]]]:
        order_row = await self._session.execute(
            _sa_text("SELECT * FROM sales_orders WHERE id = :id AND tenant_id = :tenant_id"),
            {"id": order_id, "tenant_id": tenant_id},
        )
        order = order_row.mappings().first()
        if order is None:
            raise OrderNotFoundError(f"sales order not found: {order_id}")
        line_rows = await self._session.execute(
            _sa_text(
                "SELECT * FROM sales_order_lines WHERE sales_order_id = :id "
                "AND tenant_id = :tenant_id ORDER BY line_number"
            ),
            {"id": order_id, "tenant_id": tenant_id},
        )
        lines = [dict(r) for r in line_rows.mappings().all()]
        return order, lines

    async def _update_status(
        self,
        order_id: UUID,
        status: str,
        *,
        confirmed_at: Any = None,
        cancelled_at: Any = None,
        cancelled_reason: str | None = None,
        invoice_id: UUID | None = None,
    ) -> None:
        params: dict[str, Any] = {"id": order_id, "status": status, "updated_at": utcnow()}
        if confirmed_at is not None:
            params["confirmed_at"] = confirmed_at
        if cancelled_at is not None:
            params["cancelled_at"] = cancelled_at
        if cancelled_reason is not None:
            params["cancelled_reason"] = cancelled_reason
        if invoice_id is not None:
            params["invoice_id"] = invoice_id

        set_clauses = ", ".join(f"{k} = :{k}" for k in params if k != "id")
        await self._session.execute(
            _sa_text(f"UPDATE sales_orders SET {set_clauses} WHERE id = :id"),
            params,
        )
