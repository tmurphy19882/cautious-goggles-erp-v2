"""Event envelope + topic constants (ADOPT-2).

Every event ERP v2 publishes carries the same envelope. The shape is
locked in `docs/RESEARCH_V1_PATTERNS.md § 2` and the migration to Avro
+ Apicurio (W1) will validate every payload against the matching
schema in `packages/shared-events/schemas/`.

Envelope fields (in this exact order, with this exact spelling, all
required, all flat at the top level):

    event_id     UUID  — unique per event
    event_type   str   — e.g. "scm.erp.order-confirmed.v1"
    tenant_id    UUID  — owning tenant
    occurred_at  int   — epoch milliseconds
    producer     str   — "<service>@<semver>", e.g. "erp-v2@0.1.0"
    trace_id     str   — W3C trace context trace id (32 hex chars)

Topic naming: `scm.<module>.<event-name>.v<version>`.
Customer tokenization: when the event references a customer, the field
is named `customer_token` and its value is `f"cust:{uuid}"`. Never emit
a raw `customer_id` UUID on the wire.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Producer identity (per ADOPT-2: "<service>@<semver>").
PRODUCER = "erp-v2@0.1.0"

# Topic constants — the producer must use these exact strings.
TOPIC_ORDER_CONFIRMED = "scm.erp.order-confirmed.v1"
TOPIC_INVOICE_GENERATED = "scm.erp.invoice-generated.v1"
TOPIC_PAYMENT_RECEIVED = "scm.erp.payment-received.v1"
TOPIC_PO_APPROVED = "scm.erp.po-approved.v1"
TOPIC_TENANT_CREATED = "scm.platform.tenant-created.v1"
TOPIC_CONNECTOR_CONNECTED = "scm.platform.connector-connected.v1"


def customer_token(customer_id: uuid.UUID) -> str:
    """Tokenize a customer UUID for emission on the wire (ADOPT-2 § 3)."""
    return f"cust:{customer_id}"


def now_ms() -> int:
    """Epoch milliseconds, UTC."""
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


@dataclass(slots=True)
class EventEnvelope:
    """Canonical envelope for every event ERP v2 publishes.

    Closes ADOPT-2: the envelope is the *only* shape producers and
    consumers agree on. Apicurio validation in W1 enforces this.
    """

    event_id: uuid.UUID
    event_type: str
    tenant_id: uuid.UUID
    occurred_at: int
    producer: str
    trace_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "tenant_id": str(self.tenant_id),
            "occurred_at": self.occurred_at,
            "producer": self.producer,
            "trace_id": self.trace_id,
            **self.payload,
        }


def build_envelope(
    *,
    event_id: uuid.UUID | None = None,
    event_type: str,
    tenant_id: uuid.UUID,
    trace_id: str,
    payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    """Build an envelope with sensible defaults for the W0 fields."""
    return EventEnvelope(
        event_id=event_id or uuid.uuid4(),
        event_type=event_type,
        tenant_id=tenant_id,
        occurred_at=now_ms(),
        producer=PRODUCER,
        trace_id=trace_id,
        payload=payload or {},
    )
