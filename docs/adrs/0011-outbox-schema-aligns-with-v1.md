# ADR-0011 — Outbox table schema aligns with v1

- **Status:** Accepted
- **Date:** 2026-07-11
- **Decider:** Mavis (owner) + the `w0-deep-research` worker
- **Source:** `docs/RESEARCH_V1_PATTERNS.md` § 1 + `ADOPT_NOW.md` § 3

## Context

`cautious-goggles/services/erp/src/models.py` defines an outbox with these
columns: `id`, `tenant_id`, `aggregate_type`, `aggregate_id`, `event_type`,
`topic`, `payload` (JSONB), `created_at`, `published_at`. The W0 v2
service defined the `idempotency_keys` table independently and never
created the outbox table at all (BUG-001).

W1 will ship the outbox poller + Kafka producer. If v2's outbox schema
diverges from v1's, every consumer that was already wired against v1
would need a migration when v2 ships. The merge plan in
`MERGE_PLAN.md` requires wave-by-wave cherry-pick into the parent repo;
schema drift at the wire level kills that.

## Decision

The v2 outbox table uses **the v1 column shape exactly**, plus a
`payload` JSONB column (already in v1) and an index on `published_at`
for the poller's "stuck messages" query.

Migration lands in W1 as `0200_outbox.py` (or split across W1 if
W1+ needs more). Until then, W0 ships the migration that creates the
`idempotency_keys` table (0102) which has the same RLS pattern v1
uses; the outbox migration follows the same convention.

## Consequences

- v2 W1 can drop in a poller that is functionally identical to v1's.
- Both v1 and v2 can publish to the same Kafka topic and consumers
  don't care which producer wrote the message.
- `published_at IS NULL` is the poller's "ready to send" predicate.
- Stuck-message detection: `created_at < now() - interval '5 minutes' AND published_at IS NULL`.
- A future ADR may extend the outbox with `producer` + `headers` for
  per-event tracing metadata; v1's shape is the floor, not the ceiling.
