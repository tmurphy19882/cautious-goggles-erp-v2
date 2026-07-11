# ADOPT NOW — patterns to take before W1 starts

> **Companion to** `docs/RESEARCH_V1_PATTERNS.md`. Read that first for the
> detailed analysis; this file is the *punch list* for the next 1-2 days of
> W0 cleanup before `feat/erp-v2-w0-foundations` is merged.
>
> **Scope:** everything here is in scope for W0 (i.e. we already shipped
> the W0 branch but can do a small follow-up commit on the same branch
> before merge, or land it as a W0.1 patch on top).
>
> **Five items, all P0, all small.** If we adopt these before W1 starts
> the W1 implementation is mechanical (no schema renames, no event-shape
> surprises, no consumer rework).

---

## 1. Tenant-prefix the idempotency key hash — *one-line fix*

**File:** `services/erp/src/shared/idempotency.py` (W0 ship)
**Effort:** ~30 minutes (1 line + 1 test)

v1's `hash_idempotency_key(key, *, tenant_id)` in
`packages/shared-kernel/src/shared_kernel/idempotency/store.py:16-19`
computes `sha256(f"{tenant_id}:{key}".encode("utf-8"))`. v2 W0's
`services/erp/src/shared/idempotency.py:236` computes
`sha256(key.encode("utf-8"))` — *no* tenant prefix.

**Why it matters:** two tenants that pick the same `Idempotency-Key`
(e.g. `"order-2026-07-11-001"`) currently collide in the v2 table.
Tenant A's response could be replayed to Tenant B. This is a real
multi-tenancy bug, not a theoretical one — clients building on the
default `Idempotency-Key` pattern from v1 would hit it day one.

**Adopt v1's hash exactly:**

```python
# services/erp/src/shared/idempotency.py:236
key_hash = hashlib.sha256(f"{tenant_id}:{key}".encode("utf-8")).hexdigest()
```

Plus add `test_idempotency.py::test_idempotency_keys_isolated_per_tenant`
to the unit tests.

**Blocks:** the W1 migration `0202_idempotency_keys.py`'s
`UNIQUE (tenant_id, key_hash)` constraint — the constraint is correct
but the app code's hash is wrong, so the constraint is the only thing
keeping tenants apart. Belt-and-suspenders.

---

## 2. Add the v1 envelope-shape doc + topic constants in v2 code

**Files:** `services/erp/src/o2c/envelope.py` (new), `services/erp/src/shared/events.py` (new)
**Effort:** ~1 hour

v1's six canonical envelope fields and topic naming convention are
the *contract* every consumer and every Apicurio schema speaks. W0
ships no events yet, but W1 will ship dozens. Lock the names now so
W1 is mechanical.

**Adopt v1 verbatim:**

- **Envelope fields** (in this exact order, with this exact spelling):
  `event_id`, `event_type`, `tenant_id`, `occurred_at`, `producer`,
  `trace_id`. Snake-case, all required, all flat at the top level of
  the JSON payload.
- **Topic shape:** `scm.<module>.<event-name>.v<version>`. e.g.
  `scm.erp.order-confirmed.v1`.
- **Producer format:** `<service>@<semver>`. W1 ships
  `erp-v2@0.1.0` (not `erp@0.1.0` — v2 is a new service).
- **Customer tokenization:** `customer_token = "cust:<uuid>"` — never
  emit the raw `customer_id` UUID on the wire.

**The new file `services/erp/src/o2c/envelope.py`** (a 30-line
port of `packages/shared-events/src/shared_events/envelope.py`):

```python
"""Canonical event envelope (ADR-0008)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


@dataclass(slots=True)
class EventEnvelope:
    event_id: UUID
    event_type: str
    tenant_id: UUID
    occurred_at: datetime
    producer: str
    trace_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "tenant_id": str(self.tenant_id),
            "occurred_at": int(self.occurred_at.timestamp() * 1000),
            "producer": self.producer,
            "trace_id": self.trace_id,
        }
        base.update(self.payload)
        return base


PRODUCER = "erp-v2@0.1.0"
```

**The new file `services/erp/src/shared/events.py`** (a port of v1's
`packages/shared-events/src/shared_events/topics.py`):

```python
"""Registered Kafka topic names."""
from __future__ import annotations

TOPICS: dict[str, str] = {
    "ERP_ORDER_CONFIRMED": "scm.erp.order-confirmed.v1",
    "ERP_INVOICE_GENERATED": "scm.erp.invoice-generated.v1",
    "ERP_PAYMENT_RECEIVED": "scm.erp.payment-received.v1",
    "ERP_PARTY_CREATED": "scm.erp.party-created.v1",
    "ERP_PARTY_SCREENED": "scm.erp.party-screened.v1",
    "ERP_HTS_SCHEDULE_UPDATED": "scm.erp.hts_schedule.updated.v1",
    "ERP_CUSTOMS_ENTRY_FILED": "scm.erp.customs-entry-filed.v1",
    "ERP_FTZ_INVENTORY_ADMITTED": "scm.erp.ftz-inventory.admitted.v1",
    "WMS_PICK_CONFIRMED": "scm.wms.pick-confirmed.v1",
    "TMS_SHIP_CONFIRMED": "scm.tms.ship-confirmed.v1",
    "QMS_HOLD_BLOCKS_UPDATED": "scm.qms.hold-blocks-updated.v1",
    # … rest per v1's shared-events/topics.py
}


def topic_for(key: str) -> str:
    try:
        return TOPICS[key]
    except KeyError as exc:
        raise KeyError(f"Unknown topic key: {key}") from exc
```

**Why now:** every W1 module needs to emit at least one event. If the
envelope shape and topic names are already declared in v2 with the
v1 contract, W1 is just `EventEnvelope(event_type=topic_for("ERP_ORDER_CONFIRMED"), ...).to_dict()`.

---

## 3. Make the idempotency_keys table match v1's DDL

**File:** `services/erp/migrations/versions/0102_idempotency_keys.py` (new)
**Effort:** ~1 hour

v1's `IDEMPOTENCY_KEYS_MIGRATION_SQL` in
`packages/shared-api/src/shared_api/idempotency.py:136-157` defines
the canonical shape:

```sql
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID        NOT NULL,
    key_hash        CHAR(64)    NOT NULL,
    request_hash    CHAR(64)    NOT NULL,
    response_status SMALLINT    NOT NULL,
    response_body   BYTEA       NOT NULL,
    response_headers JSONB      NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_idempotency_keys_tenant_key UNIQUE (tenant_id, key_hash)
);

CREATE INDEX IF NOT EXISTS ix_idempotency_keys_expires_at
    ON idempotency_keys (expires_at);
```

v2 W0's `services/erp/src/shared/idempotency.py:95-105` declares a
*Core* `Table` (not a migration) with a different shape:
- PK is the composite `(tenant_id, key_hash)` — no `id` column
- `response_body` is JSON not BYTEA — drops any non-JSON replays
- No `response_headers` column — drops any `Location`/`ETag` from the
  replay
- No `created_at`, no `expires_at` index — cleanup job is O(n)

**Adopt v1's DDL verbatim**, with two changes:

- Use `JSONB` not `BYTEA` for `response_body` (Postgres-native; v1's
  BYTEA choice is a CGO-era relic).
- Use `(tenant_id, key_hash)` UNIQUE constraint (already there).
- Add `response_headers JSONB` (v1's column).

**Why now:** the table doesn't exist yet (W0 uses Table reflection on
the shared metadata for in-process tests). W1's first migration
`0202_idempotency_keys.py` is the natural place — but if we land the
migration *now* as `0102_idempotency_keys.py` (after `0101_identity_rls`),
W1 doesn't need to re-touch the table. Cleaner.

**Bonus:** the `response_headers` column is required for the
`Idempotency-Key` replay contract on 201 Created responses — a
client that does `POST /orders` twice with the same key must get the
same `Location` header back.

---

## 4. Adopt v1's trade module port as the W1 baseline

**Files:** new — `services/erp/src/trade/{__init__,models,screening,hts_engine,customs,ftz,events,api}.py`
**Effort:** ~half a day (mechanical port)

The v1 trade module is the most mature code in the entire v1 repo. It
works. v2 W0's `services/erp/src/trade/` directory is *empty*. We have
two options for W1:

- (A) **Build trade from scratch in v2.** Weeks of work. We will
  inevitably introduce bugs in the HTS lookup fallback logic
  (country-specific → country-agnostic → 4-digit prefix), the
  screening risk ladder, the FTZ upsert, the outbox wiring.
- (B) **Port v1's trade verbatim to v2 W1.** Half a day. The
  behaviours are identical, the Avro schemas are identical, the
  outbox events are identical.

**Adopt (B).** The port is:

1. Copy `services/erp/src/trade/{models,screening,hts_engine,customs,ftz,events,api}.py`
   from v1 to v2, no functional change.
2. Adapt imports: `from db import Base` → `from shared.db import Base`;
   `from models import OutboxEvent` → `from o2c.outbox import OutboxEvent`
   (which W1 will add).
3. Wrap the `Decimal` fields with v2's `MoneyDecimal` (Pydantic) at
   the API boundary; the DB columns stay `Numeric(20, 4)`.
4. Add a W1 migration `0203_trade.py` with v1's table DDL (8 tables
   + 4 enums + RLS) copied verbatim.
5. Add a W1 migration `0204_trade_duty_amount.py` to add
   `duty_amount` column to `sales_order_lines` (closes audit TR-2).
6. Add `services/erp/src/o2c/hts_hook.py` that auto-resolves HTS on
   every SO line (closes audit TR-1).

**Why now:** W1 starts with "O2C complete" (per the v2 SPEC §4). The
O2C happy path needs HTS auto-resolution to land a real-world
order. If trade isn't in v2 by the W1 kickoff, O2C can't reach
TR-1/TR-2 in W1.

---

## 5. Adopt v1's `processed_events` dedup table in v2 W0 (skeleton only)

**File:** `services/erp/migrations/versions/0103_processed_events.py` (new)
**Effort:** ~30 minutes

v1 has a `processed_events` table in `services/erp/src/models.py:77-83`:

```python
class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    consumer_group: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

This is the dedup key for the `IdempotentConsumer` in
`packages/shared-kernel/src/shared_kernel/idempotency/consumer.py`.

**Adopt the table in v2 W0** as a no-op migration. W1's WMS/TMS
consumers will use it; W2's QMS consumer will use it. Without the
table, every consumer in W1 has to invent it from scratch.

**Why now:** it's free (a 6-line migration) and unblocks W1's
consumer code. There is no behavioral change.

---

## Summary of changes

| # | What | File(s) | Effort | Wave adopted in | Audit items closed |
|---|------|---------|-------:|-----------------|---------------------|
| 1 | Tenant-prefix idempotency key hash | `shared/idempotency.py` | 30 min | W0 (now) | S-15 (correctness) |
| 2 | Add envelope + topic constants | new `o2c/envelope.py`, new `shared/events.py` | 1 hr | W0 (now) / W1 (use) | S-4, OPS-7, O2C-14 |
| 3 | Idempotency DDL migration (v1 shape) | new `migrations/versions/0102_idempotency_keys.py` | 1 hr | W0 (now) / W1 (use) | S-15, O2C-4 |
| 4 | Trade module port (verbatim from v1) | new `trade/*.py` | half day | W1 (start) | TR-1, TR-2, TR-3..9 (most) |
| 5 | `processed_events` table | new `migrations/versions/0103_processed_events.py` | 30 min | W0 (now) / W1 (use) | S-2 (consumer dedup) |

Total: ~1 person-day. Five PRs, all small, all P0, all testable.

After these, the W1 implementation is a port of the v1 O2C vertical
slice (`o2c/order_flow.py`, `event_bus.py`, `outbox_poller.py`,
`consumers/{register,wms_stub,tms_stub}.py`) into v2's structure,
with no surprises.

---

## What is explicitly *not* in ADOPT_NOW (deferred to W1+)

- Saga orchestrator code (W1) — the v1 `SagaOrchestrator` is 245
  lines; porting it as-is is fine but it needs the W1 multi-line
  order flow to exercise. Defer to W1.
- HoldGate (W2) — needs the QMS service consumer first.
- Hash chain audit log (W6) — needs the audit log read API.
- `extract_tenant_from_jwt` (W1) — needs JWT first.
- Apicurio client (W1 in-process / W10 CI gate) — needs the W1 event
  production to be real.
- DLQ table (W1) — needs the W1 outbox poller.
- Frontend integration (W6) — needs the v2 backend to actually be
  production-routed, which is W6.
