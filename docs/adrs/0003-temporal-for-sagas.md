# ADR-0003 — Temporal for O2C and P2P sagas

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | **W0 defers** the runtime; W1 wires O2C; W2 wires P2P |
| Supersedes | v1's in-process `o2c/order_flow.py` |
| Related | [ADR-0004 — outbox / Apicurio](./0004-outbox-avro-apicurio.md), [`SPEC.md`](../SPEC.md#wave-1) |

## Context

O2C and P2P are multi-step sagas with compensation:

- **O2C:** confirm SO → credit check → reserve inventory → ship
  (WMS event) → invoice → payment → GL post. If any step fails, the
  prior steps must roll back (release reservation, reverse credit
  hold, void SO).
- **P2P:** requisition → approval → PO → receipt (WMS event) →
  3-way match → AP. If 3-way match fails, hold the receipt and
  notify the buyer.

v1 implements O2C as a single FastAPI handler (`o2c/order_flow.py`)
with a TODO list and no compensation. If the DB call for reservation
fails after the credit check has been called, the order is left in a
`confirmed` state with no inventory reserved. Recovery is manual.

Options for the v2 saga runtime:

- **Celery + Redis.** Familiar, but the saga/compensation story
  requires bolting on a workflow DSL; the visibility and signal APIs
  are poor; retries on partial failure are manual.
- **AWS Step Functions.** Strong for AWS-resident workflows; couples
  us to the AWS control plane; no local-dev parity.
- **Custom in-process orchestrator.** v1's path. Cheap to start,
  no operational story, no retries, no compensation, no visibility.
- **Temporal.** Purpose-built for long-running workflows with
  compensation. Has a local-dev container; signals, queries, and
  timers are first-class; durability and visibility are handled.

## Decision

We use **Temporal** as the saga runtime for O2C (W1) and P2P (W2).

- The workflow definitions live in `o2c/workflows/order_to_cash.py`
  and `p2p/workflows/procure_to_pay.py`. They orchestrate *activity*
  calls into the in-process service classes; the activities do the
  real DB and outbound-event work, with the outbox.
- Compensation is a Temporal feature: each activity has a
  corresponding `*_compensation` activity that runs in reverse order
  on saga failure.
- Local dev ships a Temporal dev server container; the v2 service
  talks to it via `temporalio` (Python SDK). The dev container is
  *not* required for W0 — the W0 boot path runs without Temporal.
- Workflow identity is derived from the business key
  (`so-{sales_order_id}`) so a duplicate API call doesn't start a
  second workflow.

## Consequences

### Positive

- A failed saga auto-compensates (release reservation, reverse
  credit hold, etc.). The "SO confirmed but no reservation" class
  of bug that v1 ships is structurally impossible.
- Workflow state is queryable via the Temporal UI / CLI. Operators
  can see exactly which step a stuck O2C is on without reading
  application logs.
- Signals (e.g. `cancel`, `apply-payment`) and timers
  (e.g. `release-credit-hold-after-7-days`) are first-class.
- The Python SDK gives us typed workflow + activity definitions
  with Pydantic-style payloads.

### Negative / costs

- **Local dev needs a Temporal container.** W0 ships without it.
  W1+ local dev requires `docker compose up temporal` alongside
  Postgres. The dev experience is a step more complex than v1's
  `uvicorn src.app:app` and `curl`. Documented in
  [`GETTING_STARTED.md`](../GETTING_STARTED.md).
- **A second operational dependency.** Production needs a
  Temporal cluster (self-hosted or Temporal Cloud). Adds a
  component to monitor and a new failure mode to alert on.
- **Workflow versioning.** Schema changes to in-flight workflows
  need `temporal.workflow.continue_as_new` or version branches.
  Not free; not a blocker, just a discipline to keep.
- **Outbox + Temporal is belt + suspenders.** The outbox is the
  durability boundary; the workflow is the orchestration. Either
  alone would be weaker; both is the right cost. Documented in
  [ADR-0004](./0004-outbox-avro-apicurio.md).

### What we lose

- The "single handler, no infra" simplicity of v1. O2C and P2P now
  need a running Temporal cluster in dev and prod. The trade is
  correctness: any O2C that completes is fully compensated on
  failure, and any O2C that's in flight is queryable.
- Legacy in-process sagas in v1 are not migrated. They stay
  behind `erp.v2.o2c.enabled` and `erp.v2.p2p.enabled` flags until
  W10 retires them.

### W0 deferral

W0 ships:

- The outbox *table* (W1+ adds the poller; W0 only has the
  envelope).
- Empty `o2c/` and `p2p/` Python packages (so the import graph
  is reviewable).
- A `temporal` extra in `pyproject.toml` (optional `[temporal]`
  install group).

W0 does **not** ship:

- The Temporal dev container (`infra/docker-compose.yml`).
- Any `temporalio` import in the boot path.
- Workflow or activity code.
