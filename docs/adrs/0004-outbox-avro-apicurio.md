# ADR-0004 — Outbox pattern + Apicurio for every event

| Field | Value |
|-------|-------|
| Status | **Accepted** (W0 defers the poller + producer) |
| Date | 2026-07-11 |
| Wave | **W0:** table + envelope; **W1:** poller + producer + CI gate |
| Supersedes | v1's in-process pub/sub |
| Related | [ADR-0003 — Temporal](./0003-temporal-for-sagas.md), [`SPEC.md`](../SPEC.md) |

## Context

Every domain state change in v2 has to publish at least one event
(`ORDER_CONFIRMED`, `INVOICE_GENERATED`, `PAYMENT_RECEIVED`,
`PO_APPROVED`, `*_UPDATED`, …). v1 does this with an in-process
pub/sub bus. The bus has no durability, no replay, no schema
contract, and no way to debug "event X was supposed to fire but
didn't."

Two distinct problems need solving:

1. **Atomicity.** Writing the state change and emitting the event
   must be in the same transaction. If the DB write commits and the
   event publish fails, downstream services (WMS, TMS, QMS, MRP) act
   on stale data. If the event publishes but the DB write rolls
   back, downstream acts on a phantom event.
2. **Schema contract.** The event payload that ERP produces must be
   the event payload the consumer expects. Today this is enforced
   by hand-waving and a comment in `services/erp/README.md`.

Options:

- **Two-phase commit (XA).** Works, kills throughput, no native
  support in our driver stack.
- **Direct Kafka publish in the request handler.** Fails the
  atomicity test; if Kafka is down, the API fails or the event is
  lost depending on which side of the transaction we put it.
- **Outbox table.** Industry-standard solution to (1). Add a row
  to `outbox_events` in the same transaction as the state change.
  A separate poller reads un-published rows and ships to Kafka.
  Loss window is "between the row insert and the poller pick" —
  solved by the poller running on a short interval and
  idempotency at the consumer.
- **Apicurio Registry** for (2). Schema registry, Avro/JSON-Schema
  payloads, a CI gate (`validate_manifest.py`) that refuses merges
  with drift between producer schemas and registered versions.
- **Confluent Schema Registry.** Same shape; Confluent-specific.
  Apicurio is open source, has a clean Python client, and doesn't
  bind us to Confluent Cloud.

## Decision

- **Outbox pattern for every domain event.** Every state change
  writes a row to `outbox_events` in the same transaction. W1 adds
  the poller (`outbox_poller`) and the producer; W0 only ships the
  table and the in-process envelope so the import graph is
  reviewable.
- **Apicurio Registry** for the schema contract. Every event topic
  has an Avro schema at `packages/shared-events/schemas/<topic>.v1.avsc`
  (managed in the parent repo). The producer serialises via
  Apicurio's `aio` client and reads the schema ID from the registry
  to embed in the Kafka message headers.
- **Idempotency at the consumer.** Every event carries a
  `event_id` (UUIDv7). Consumers use the standard
  `deduplicate-by-event-id` pattern so the at-least-once
  semantics of the outbox poller don't cause duplicate side effects.
- **CI gate `validate_manifest.py`** refuses merges with drift
  between any `*.avsc` file and the registry's current version.
  Soft-fail in W1, hard-fail in W10.

## Consequences

### Positive

- Atomicity: outbox row + state change are one transaction.
  Either both commit or both don't.
- Replay: a misbehaving consumer can re-read from the outbox
  table (it stores a `created_at` and a `published_at`).
- Schema contract: every event payload is validated against the
  Avro schema before it hits Kafka. Bad payloads fail in the
  producer, not in the consumer.
- Drift detection: CI fails a PR that changes an event shape
  without bumping the schema version.

### Negative / costs

- **Latency floor.** A poller running every 100ms adds up to
  100ms of event-delivery latency. W1 measures the actual
  number and exposes `outbox_publish_latency_seconds` so it can
  be alerted on.
- **Operational dependency on Apicurio.** The producer can't
  start without a reachable registry. W0's `dump_openapi.py`
  uses a local file; W1's `outbox_poller` needs the registry
  up. CI runs a local Apicurio for the gate.
- **Schema evolution discipline.** A change to a payload
  requires a new `.v2.avsc` file, a `version` bump in the topic
  name, and consumer coordination. This is the desired cost
  (no silent breaking changes), but it's work that didn't exist
  in v1.
- **One more table to migrate.** `outbox_events` lives in
  `services/erp/migrations/versions/0102_outbox.py` (W1).

### W0 deferral

W0 ships:

- `outbox_events` row shape in `shared/outbox.py` (the in-process
  envelope — `OutboxEvent`, `OutboxStore`).
- A doc-only stub in `docs/waves/WAVE_0_FOUNDATIONS.md`.

W0 does **not** ship:

- The poller loop.
- The Kafka producer.
- The Apicurio client wrapper (W1).

This keeps the W0 boot path free of an outbox/Kafka/Apicurio
dependency. W1's first PR is the poller + producer + Apicurio
client.
