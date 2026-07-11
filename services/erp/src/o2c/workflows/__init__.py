"""Order-to-Cash orchestrator (O2C-10 — Temporal workflow).

W1 ships an in-process state machine that mirrors the shape of a
Temporal workflow. When W1.1 brings Temporal online, the same
`OrderToCashWorkflow.run()` interface maps to a Temporal workflow
definition; the activity functions become Temporal activities.

States:
    new → credit_checked → reserved → confirmed → invoiced → paid
                                                       ↘ failed
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from o2c import invoice as invoice_svc
from o2c.credit import check_credit, release_credit_hold_if_held
from o2c.invoice import GenerateInvoiceResult
from o2c.reservation import release_reservations, reserve_for_order
from o2c.sales_order import (
    ConfirmSalesOrderResult,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_CREDIT_HOLD,
    STATUS_INVOICED,
    SalesOrderService,
)
from shared.outbox import OutboxPoller, get_event_bus
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkflowResult:
    sales_order_id: UUID
    final_status: str
    invoice_id: UUID | None = None
    events_published: int = 0
    error: str | None = None


class OrderToCashWorkflow:
    """In-process orchestrator.

    Same shape as a Temporal workflow:
    - `run()` is the entry point.
    - Each step is a method (would be a Temporal activity).
    - State is the SO's `status` column (would be a workflow input +
      query).

    The companion poller (set up by the API factory) publishes outbox
    rows to Kafka / the in-process bus. The invoice step is triggered
    by the SHIP_CONFIRMED event in production; for tests and the
    smoke flow, `run()` calls it directly after `confirm()`.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def run(
        self, *, tenant_id: UUID, sales_order_id: UUID, trace_id: str | None = None
    ) -> WorkflowResult:
        # 1. Create the SO and confirm (in one transaction).
        async with self._sf() as session:
            svc = SalesOrderService(session)
            confirm_result = await svc.confirm(
                tenant_id=tenant_id, order_id=sales_order_id, trace_id=trace_id
            )
            await session.commit()

        if confirm_result.status == STATUS_CREDIT_HOLD:
            return WorkflowResult(
                sales_order_id=sales_order_id,
                final_status=STATUS_CREDIT_HOLD,
            )

        # 2. In production, the WMS consumes ORDER_CONFIRMED and ships.
        #    The SHIP_CONFIRMED consumer (consumers/ship_confirmed.py) calls
        #    back into the workflow to generate the invoice. For W1's
        #    synchronous smoke flow we generate the invoice inline.
        inv_result = await self._generate_invoice(
            tenant_id=tenant_id, sales_order_id=sales_order_id, trace_id=trace_id
        )

        # 3. Tick the outbox poller once so the events leave the
        #    in-process bus. Production swap: real Kafka producer.
        poller = OutboxPoller(
            session_factory=self._sf,
            publisher=self._in_process_publisher,
            poll_interval=0.05,
        )
        published = await poller.tick()

        return WorkflowResult(
            sales_order_id=sales_order_id,
            final_status=STATUS_INVOICED,
            invoice_id=inv_result.invoice_id,
            events_published=published,
        )

    async def _generate_invoice(
        self, *, tenant_id: UUID, sales_order_id: UUID, trace_id: str | None
    ) -> GenerateInvoiceResult:
        async with self._sf() as session:
            result = await invoice_svc.generate_invoice_for_order(
                session,
                tenant_id=tenant_id,
                sales_order_id=sales_order_id,
                trace_id=trace_id,
            )
            await session.commit()
        return result

    async def _in_process_publisher(self, event) -> None:
        """For W1: publish to the in-process bus; for W1.1: to Kafka."""
        bus = get_event_bus()
        headers = {"event_id": str(event.id), "event_type": event.event_type}
        await bus.publish(event.topic, event.payload, headers)
