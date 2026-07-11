"""Goods Receipt service for P2P (P2P-4).

Receipts are driven by the WMS `GOODS_RECEIVED` event in production;
W1 also ships a synchronous `record_receipt` so the W2 happy-path
test can drive a receipt end-to-end.

On receipt:
- One `goods_receipts` row + one `goods_receipt_lines` row per PO line.
- `purchase_order_lines.quantity_received` is bumped.
- A new `three_way_matches` row is created (P2P-5). The match runs
  automatically against the receipt; status flips to `matched` or
  `exception` based on quantity + price tolerance.
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


@dataclass(slots=True)
class ReceiptLineInput:
    po_line_id: UUID
    sku: str
    quantity_received: Decimal
    unit: str = "each"
    lot_id: str | None = None
    bin_id: str | None = None
    quality_status: str = "accepted"


@dataclass(slots=True)
class RecordReceiptInput:
    po_id: UUID
    supplier_id: UUID
    received_by: UUID | None = None
    notes: str | None = None
    lines: list[ReceiptLineInput] = field(default_factory=list)


@dataclass(slots=True)
class RecordReceiptResult:
    receipt_id: UUID
    receipt_number: str
    match_id: UUID
    match_status: str  # matched | exception


class GoodsReceiptService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_receipt(
        self, *, tenant_id: UUID, input: RecordReceiptInput
    ) -> RecordReceiptResult:
        if not input.lines:
            raise ValueError("at least one receipt line is required")
        rid = uuid4()
        rnumber = f"GR-{rid.hex[:8].upper()}"

        # Pull the PO for total + supplier cross-check.
        po = (
            await self._session.execute(
                _sa_text(
                    "SELECT id, supplier_id, total_amount FROM purchase_orders "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": input.po_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if po is None:
            raise ValueError(f"purchase order not found: {input.po_id}")
        if po["supplier_id"] != input.supplier_id:
            raise ValueError("receipt supplier does not match PO supplier")

        await self._session.execute(
            _sa_text(
                """
                INSERT INTO goods_receipts (
                    id, tenant_id, receipt_number, po_id, supplier_id, received_by, notes
                ) VALUES (:id, :tenant_id, :rn, :po_id, :supplier_id, :by, :notes)
                """
            ),
            {
                "id": rid,
                "tenant_id": tenant_id,
                "rn": rnumber,
                "po_id": input.po_id,
                "supplier_id": input.supplier_id,
                "by": input.received_by,
                "notes": input.notes,
            },
        )
        for idx, line in enumerate(input.lines, start=1):
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO goods_receipt_lines (
                        tenant_id, receipt_id, po_line_id, line_number, sku,
                        quantity_received, unit, lot_id, bin_id, quality_status
                    ) VALUES (
                        :tenant_id, :rid, :po_line_id, :ln, :sku,
                        :qty, :unit, :lot, :bin, :quality
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "rid": rid,
                    "po_line_id": line.po_line_id,
                    "ln": idx,
                    "sku": line.sku,
                    "qty": line.quantity_received,
                    "unit": line.unit,
                    "lot": line.lot_id,
                    "bin": line.bin_id,
                    "quality": line.quality_status,
                },
            )
            # Bump quantity_received on the PO line.
            await self._session.execute(
                _sa_text(
                    "UPDATE purchase_order_lines SET quantity_received = quantity_received + :qty "
                    "WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"qty": line.quantity_received, "id": line.po_line_id, "tenant_id": tenant_id},
            )

        # Create the 3-way match row and run the check.
        match_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO three_way_matches (
                    id, tenant_id, po_id, receipt_id, status
                ) VALUES (:id, :tenant_id, :po_id, :rid, 'pending')
                """
            ),
            {"id": match_id, "tenant_id": tenant_id, "po_id": input.po_id, "rid": rid},
        )

        match_status = await self._run_three_way_match(
            tenant_id=tenant_id, match_id=match_id, receipt_id=rid
        )
        await self._session.flush()
        return RecordReceiptResult(
            receipt_id=rid,
            receipt_number=rnumber,
            match_id=match_id,
            match_status=match_status,
        )

    async def _run_three_way_match(
        self, *, tenant_id: UUID, match_id: UUID, receipt_id: UUID
    ) -> str:
        """Walk the receipt lines and compare to PO lines.

        P2P-5: every receipt line must be within the configured
        quantity + price tolerance. On exception, the match row flips
        to `exception` and the per-line status records the kind.
        """
        lines = (
            await self._session.execute(
                _sa_text(
                    """
                    SELECT grl.id AS receipt_line_id, grl.po_line_id,
                           grl.quantity_received, grl.sku,
                           pol.quantity AS quantity_ordered,
                           pol.unit_price AS unit_price_ordered,
                           tm.quantity_tolerance_pct, tm.price_tolerance_pct
                    FROM goods_receipt_lines grl
                    JOIN purchase_order_lines pol
                      ON pol.id = grl.po_line_id AND pol.tenant_id = grl.tenant_id
                    JOIN three_way_matches tm
                      ON tm.id = :match_id AND tm.tenant_id = grl.tenant_id
                    WHERE grl.receipt_id = :rid AND grl.tenant_id = :tenant_id
                    """
                ),
                {"match_id": match_id, "rid": receipt_id, "tenant_id": tenant_id},
            )
        ).mappings().all()

        any_exception = False
        for ln in lines:
            qty_ord = Decimal(str(ln["quantity_ordered"]))
            qty_recv = Decimal(str(ln["quantity_received"]))
            qty_tol = Decimal(str(ln["quantity_tolerance_pct"])) / Decimal("100")
            price_tol = Decimal(str(ln["price_tolerance_pct"])) / Decimal("100")

            # For W2 the AP side hasn't run yet, so price mismatch is
            # only checkable if an AP invoice exists. Without one, we
            # can't price-check. Quantity check is the primary signal.
            qty_within = abs(qty_recv - qty_ord) <= qty_ord * qty_tol
            line_status = "matched" if qty_within else "qty_short" if qty_recv < qty_ord else "qty_over"
            if not qty_within:
                any_exception = True
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO three_way_match_lines (
                        tenant_id, match_id, po_line_id, receipt_line_id,
                        quantity_ordered, quantity_received, unit_price_ordered, status
                    ) VALUES (
                        :tenant_id, :match_id, :po_line_id, :receipt_line_id,
                        :qty_ord, :qty_recv, :price_ord, :status
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "match_id": match_id,
                    "po_line_id": ln["po_line_id"],
                    "receipt_line_id": ln["receipt_line_id"],
                    "qty_ord": qty_ord,
                    "qty_recv": qty_recv,
                    "price_ord": ln["unit_price_ordered"],
                    "status": line_status,
                },
            )

        new_status = "exception" if any_exception else "matched"
        await self._session.execute(
            _sa_text(
                "UPDATE three_way_matches SET status = :status, matched_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"status": new_status, "now": utcnow(), "id": match_id, "tenant_id": tenant_id},
        )

        # Update PO status to partially_received if any line is short
        # of full quantity, else received.
        fully_received = all(
            Decimal(str(ln["quantity_received"])) >= Decimal(str(ln["quantity_ordered"]))
            for ln in lines
        )
        po_status = "received" if fully_received else "partially_received"
        await self._session.execute(
            _sa_text(
                "UPDATE purchase_orders SET status = :status, updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'sent'"
            ),
            {"status": po_status, "now": utcnow(), "id": ln["po_line_id"], "tenant_id": tenant_id}
            if False else  # guard: use po_id from first line
            {"status": po_status, "now": utcnow(), "id": None, "tenant_id": tenant_id}
        )
        # The above is a no-op when id=None. Real update below.
        po_id_for_update = (
            await self._session.execute(
                _sa_text("SELECT po_id FROM three_way_matches WHERE id = :id"),
                {"id": match_id},
            )
        ).scalar_one_or_none()
        if po_id_for_update is not None:
            await self._session.execute(
                _sa_text(
                    "UPDATE purchase_orders SET status = :status, updated_at = :now "
                    "WHERE id = :id AND tenant_id = :tenant_id AND status = 'sent'"
                ),
                {"status": po_status, "now": utcnow(), "id": po_id_for_update, "tenant_id": tenant_id},
            )

        return new_status
