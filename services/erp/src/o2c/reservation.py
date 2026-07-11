"""Inventory reservation for O2C (O2C-3).

W1 ships a per-tenant, per-product, per-location reservation model.
`reserve_for_order` creates `inventory_reservations` rows when an
order is confirmed. `release_reservations` marks them released on
cancel.

A real implementation would query the WMS on-hand projection; W1
treats the WMS as the source of truth and writes the reservation as
a soft hold. W1's WMS is in-process (`shared.outbox.EventBus`),
so the reservation is a logical claim, not a physical one.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow


async def reserve_for_order(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    sales_order_id: UUID,
    lines: list[dict[str, Any]],
) -> None:
    """Create an `inventory_reservations` row per line that has a
    `ship_from_location_id` (lines without a location are drop-ship
    and don't get a reservation).
    """
    for line in lines:
        location_id = line.get("ship_from_location_id")
        if location_id is None:
            continue
        await session.execute(
            _sa_text(
                """
                INSERT INTO inventory_reservations (
                    tenant_id, sales_order_id, product_id, location_id,
                    quantity, status
                ) VALUES (
                    :tenant_id, :sales_order_id, :product_id, :location_id,
                    :quantity, 'active'
                )
                """
            ),
            {
                "tenant_id": tenant_id,
                "sales_order_id": sales_order_id,
                "product_id": line["product_id"],
                "location_id": location_id,
                "quantity": line["quantity"],
            },
        )


async def release_reservations(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    sales_order_id: UUID,
    reason: str,
) -> None:
    """Mark every active reservation for the order as released."""
    await session.execute(
        _sa_text(
            "UPDATE inventory_reservations SET status = 'released', "
            "released_at = :released_at, released_reason = :reason "
            "WHERE tenant_id = :tenant_id AND sales_order_id = :sales_order_id "
            "AND status = 'active'"
        ),
        {
            "released_at": utcnow(),
            "reason": reason,
            "tenant_id": tenant_id,
            "sales_order_id": sales_order_id,
        },
    )
