"""Consumers for ERP v2 W1 — in-process subscribers to the event bus.

WMS/TMS consumers (in-process until W1.1 brings Kafka online):

- `ship_confirmed_consumer`: subscribes to `scm.wms.shipment-confirmed.v1`
  (or `scm.tms.ship-confirmed.v1`). On a ship event, looks up the
  related SO and triggers the invoice-generation step. In the W1
  synchronous smoke flow, this is also called inline by the workflow
  after `confirm()`.

- `supplier_suspended_consumer`: subscribes to
  `scm.qms.supplier-suspended.v1` and places a `hold` on the
  affected party's open orders. (O2C-5 + BUG-001's
  SUPPLIER_SUSPENDED event.)

W1 ships the consumers as plain async functions; W1.1 wires them
into a real Kafka consumer group via `shared.outbox.EventBus.subscribe`.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from o2c.invoice import generate_invoice_for_order
from o2c.sales_order import (
    STATUS_CONFIRMED,
    STATUS_HELD,
    SalesOrderService,
)
from shared.outbox import get_event_bus
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


TOPIC_SHIPMENT_CONFIRMED = "scm.wms.shipment-confirmed.v1"
TOPIC_SHIP_CONFIRMED = "scm.tms.ship-confirmed.v1"
TOPIC_HOLD_PLACED = "scm.qms.hold-placed.v1"
TOPIC_SUPPLIER_SUSPENDED = "scm.qms.supplier-suspended.v1"


def register_consumers(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Wire the W1 consumers onto the in-process event bus."""
    bus = get_event_bus()
    bus.subscribe(TOPIC_SHIPMENT_CONFIRMED, _make_ship_consumer(session_factory))
    bus.subscribe(TOPIC_SHIP_CONFIRMED, _make_ship_consumer(session_factory))
    bus.subscribe(TOPIC_HOLD_PLACED, _make_hold_consumer(session_factory))
    bus.subscribe(TOPIC_SUPPLIER_SUSPENDED, _make_supplier_suspended_consumer(session_factory))


def _make_ship_consumer(session_factory: async_sessionmaker[AsyncSession]):
    async def consumer(topic: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
        order_id_str = payload.get("order_id")
        if not order_id_str:
            logger.warning("ship consumer: missing order_id in payload")
            return
        order_id = UUID(order_id_str)
        # Pull the tenant_id from the payload (shipment events are
        # tenant-scoped, set by the WMS / TMS).
        tenant_id_str = payload.get("tenant_id")
        if not tenant_id_str:
            logger.warning("ship consumer: missing tenant_id in payload")
            return
        tenant_id = UUID(tenant_id_str)
        async with session_factory() as session:
            try:
                await generate_invoice_for_order(
                    session,
                    tenant_id=tenant_id,
                    sales_order_id=order_id,
                    trace_id=headers.get("trace_id"),
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.warning("ship consumer failed: %s", exc)

    return consumer


def _make_hold_consumer(session_factory: async_sessionmaker[AsyncSession]):
    async def consumer(topic: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
        subject_type = payload.get("subject_type")
        subject_id = payload.get("subject_id")
        reason = payload.get("reason") or "hold placed by upstream system"
        if not (subject_type and subject_id):
            return
        tenant_id = UUID(payload["tenant_id"])
        sid = UUID(subject_id)
        async with session_factory() as session:
            try:
                await session.execute(
                    _sa_text(
                        """
                        INSERT INTO holds (
                            tenant_id, subject_type, subject_id, reason, source, source_event_id
                        ) VALUES (
                            :tenant_id, :subject_type, :subject_id, :reason, 'qms', :event_id
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "subject_type": subject_type,
                        "subject_id": sid,
                        "reason": reason,
                        "event_id": UUID(headers.get("event_id")) if headers.get("event_id") else None,
                    },
                )
                if subject_type == "sales_order":
                    svc = SalesOrderService(session)
                    await svc._update_status(sid, STATUS_HELD)  # type: ignore[attr-defined]
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.warning("hold consumer failed: %s", exc)

    return consumer


def _make_supplier_suspended_consumer(session_factory: async_sessionmaker[AsyncSession]):
    async def consumer(topic: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
        # P2P-side concern; O2C watches only because a suspended supplier
        # is a reason to place a procurement hold (which is upstream of
        # O2C's stock expectations). W1 logs and stores the event as a
        # `holds` row on the supplier party.
        party_id = payload.get("supplier_id")
        if not party_id:
            return
        tenant_id = UUID(payload["tenant_id"])
        async with session_factory() as session:
            try:
                await session.execute(
                    _sa_text(
                        """
                        INSERT INTO holds (
                            tenant_id, subject_type, subject_id, reason, source, source_event_id
                        ) VALUES (
                            :tenant_id, 'supplier', :party_id,
                            :reason, 'qms', :event_id
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "party_id": UUID(party_id),
                        "reason": payload.get("reason") or "supplier suspended",
                        "event_id": UUID(headers.get("event_id")) if headers.get("event_id") else None,
                    },
                )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.warning("supplier_suspended consumer failed: %s", exc)

    return consumer
