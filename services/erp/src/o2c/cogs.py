"""FIFO COGS allocation (O2C-9) + revenue recognition stub (O2C-8).

W1's `allocate_cogs_for_invoice` walks the `inventory_valuation`
table in FIFO order (`layer_date ASC`) and consumes layers until
the requested quantity is fulfilled. Unfulfilled quantity is
recorded as a "backorder" for W1.1 to handle.

`recognize_revenue` is a no-op stub for W1; the scheduled job and
recognition rules land in W4 (Finance).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow


@dataclass(slots=True)
class CogsAllocation:
    """Per-line COGS allocation result."""

    sales_order_line_id: UUID
    sku: str
    quantity: Decimal
    unit_cost: Decimal
    cogs_total: Decimal
    backorder: Decimal = Decimal("0")


async def allocate_cogs_for_invoice(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    lines: list[dict[str, Any]],
) -> list[CogsAllocation]:
    """For each line, walk the FIFO layers and consume up to the
    line's quantity. Returns one `CogsAllocation` per line, in order.
    """
    out: list[CogsAllocation] = []
    for line in lines:
        sku = line["sku"]
        qty_needed = Decimal(str(line["quantity"]))
        # Pull the open FIFO layers for this product, oldest first.
        rows = (
            await session.execute(
                _sa_text(
                    """
                    SELECT id, quantity, unit_cost FROM inventory_valuation
                    WHERE tenant_id = :tenant_id
                      AND product_id = :product_id
                      AND depleted_at IS NULL
                    ORDER BY layer_date
                    """
                ),
                {"tenant_id": tenant_id, "product_id": line["product_id"]},
            )
        ).mappings().all()

        remaining = qty_needed
        weighted_cost = Decimal("0")
        consumed = Decimal("0")
        for r in rows:
            if remaining <= Decimal("0"):
                break
            layer_qty = Decimal(str(r["quantity"]))
            layer_cost = Decimal(str(r["unit_cost"]))
            take = min(remaining, layer_qty)
            weighted_cost += take * layer_cost
            consumed += take
            remaining -= take
            if take >= layer_qty:
                # Layer depleted.
                await session.execute(
                    _sa_text(
                        "UPDATE inventory_valuation SET quantity = 0, depleted_at = :now WHERE id = :id"
                    ),
                    {"now": utcnow(), "id": r["id"]},
                )
            else:
                await session.execute(
                    _sa_text(
                        "UPDATE inventory_valuation SET quantity = quantity - :take WHERE id = :id"
                    ),
                    {"take": take, "id": r["id"]},
                )

        unit_cost = (weighted_cost / consumed) if consumed > 0 else Decimal("0")
        out.append(
            CogsAllocation(
                sales_order_line_id=line["id"],
                sku=sku,
                quantity=qty_needed,
                unit_cost=unit_cost,
                cogs_total=weighted_cost,
                backorder=remaining,
            )
        )
    return out


async def recognize_revenue(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    invoice_id: UUID,
) -> None:
    """O2C-8 stub. W4 ships the rule engine + scheduled job."""
    # No-op in W1.
    return None
