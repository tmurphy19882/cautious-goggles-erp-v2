"""Outbox infrastructure for ERP v2.

Implements the transactional outbox pattern (ADR-0004, ADR-0011):

- `OutboxEvent` model matches the v1 schema exactly.
- `OutboxStore` writes events in the same transaction as the
  business state change.
- `OutboxPoller` is the background worker that reads unpublished
  events and publishes them to Kafka. W1 ships a no-op poller that
  marks events as published in-process; W1.1 swaps in the real Kafka
  producer (after the test harness can stand up Redpanda).
- `EventBus` is the in-process pub/sub used by:
  - WMS/TMS stub consumers (in-process) when Kafka isn't available
  - Tests for deterministic, fast event handling
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from shared.db import Base
from shared.events import EventEnvelope
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


class OutboxEvent(Base):
    """Outbox row — matches v1's `services/erp/src/models.py:OutboxEvent` exactly.

    ADR-0011: the schema is the same as v1 so a poller / consumer written
    against v1 works against v2.
    """

    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_outbox_events_unpublished", "published_at", "created_at"),
    )


class OutboxStore:
    """Write-side API for the outbox."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emit(
        self,
        *,
        tenant_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        envelope: EventEnvelope,
        topic: str,
    ) -> UUID:
        """Insert an outbox row in the current transaction."""
        event = OutboxEvent(
            id=UUID(envelope.event_id) if isinstance(envelope.event_id, str) else envelope.event_id,
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=envelope.event_type,
            topic=topic,
            payload=envelope.to_dict(),
        )
        self._session.add(event)
        await self._session.flush()
        return event.id


class OutboxPoller:
    """Background poller that publishes unpublished outbox rows.

    W1 ships a stub poller that:
    - Polls every `poll_interval` seconds.
    - For each unpublished row, calls `publisher(row)`.
    - Marks `published_at` on success.
    - W1.1 swaps `publisher` for a real Kafka producer.

    Usage:

        poller = OutboxPoller(session_factory, publisher=..., poll_interval=0.1)
        await poller.run_forever()
    """

    def __init__(
        self,
        session_factory: Any,  # async_sessionmaker[AsyncSession]
        publisher: Callable[[OutboxEvent], Awaitable[None]],
        poll_interval: float = 0.5,
        batch_size: int = 50,
    ) -> None:
        self._sf = session_factory
        self._publisher = publisher
        self._poll_interval = poll_interval
        self._batch_size = batch_size

    async def tick(self) -> int:
        """Run one poll cycle. Returns the number of events published."""
        async with self._sf() as session:
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at)
                .limit(self._batch_size)
            )
            rows = (await session.execute(stmt)).scalars().all()
            count = 0
            for row in rows:
                try:
                    await self._publisher(row)
                except Exception as exc:  # pragma: no cover
                    logger.warning("outbox publish failed: %s", exc)
                    continue
                row.published_at = utcnow()
                count += 1
            await session.commit()
            return count

    async def run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self._poll_interval)


# --- In-process event bus (for WMS/TMS stub consumers and tests) ---

SubscriberFn = Callable[[str, dict[str, Any], dict[str, str]], Awaitable[None]]


class EventBus:
    """Tiny in-process pub/sub. W1's WMS/TMS stubs use this; W1.1 replaces
    with Kafka consumers.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[SubscriberFn]] = {}

    def subscribe(self, topic: str, fn: SubscriberFn) -> None:
        self._subscribers.setdefault(topic, []).append(fn)

    async def publish(self, topic: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        headers = headers or {}
        for fn in list(self._subscribers.get(topic, [])):
            try:
                await fn(topic, payload, headers)
            except Exception as exc:  # pragma: no cover
                logger.warning("event bus subscriber failed: %s", exc)


# Singleton for the process. Replaced in tests via `set_event_bus`.
_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS


def set_event_bus(bus: EventBus) -> None:
    global _BUS
    _BUS = bus
