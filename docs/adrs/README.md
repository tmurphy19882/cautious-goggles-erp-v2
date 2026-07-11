# Architectural Decision Records — ERP v2

ADRs that govern v2. Each is a one-page `NNNN-title.md` with: Context, Decision, Consequences.

| # | Title | File | Status | Summary |
|---|-------|------|--------|---------|
| 0001 | Wave-based delivery, no big-bang cutover | [`0001-wave-based-delivery.md`](./0001-wave-based-delivery.md) | **Accepted** | One wave = one PR into the parent's `feat/erp-crm`; W0 ships plumbing only, no behaviour. |
| 0002 | Pydantic v2 + SQLAlchemy 2.0 async + Alembic | [`0002-pydantic-v2-sqlalchemy-2-async-alembic.md`](./0002-pydantic-v2-sqlalchemy-2-async-alembic.md) | **Accepted** | Pydantic v2 schemas, async SQLAlchemy 2.0 + asyncpg, Alembic 1.13 async env. Drop sync tooling. |
| 0003 | Temporal for O2C and P2P sagas | [`0003-temporal-for-sagas.md`](./0003-temporal-for-sagas.md) | **Accepted** (W0 defers runtime; W1 wires O2C, W2 wires P2P) | Long-running workflows with first-class compensation; local dev needs a Temporal container. |
| 0004 | Outbox pattern + Apicurio for every event | [`0004-outbox-avro-apicurio.md`](./0004-outbox-avro-apicurio.md) | **Accepted** (W0 ships the table; W1 ships the poller + producer) | Outbox row in the same transaction as the state change; Apicurio validates the Avro contract. |
| 0005 | RBAC on every mutator | [`0005-rbac-on-every-mutator.md`](./0005-rbac-on-every-mutator.md) | **Accepted** (W0 ships table + service; W1 wires real JWT) | One chokepoint (`PermissionService.assert_can`) + dep factory + static guard. |
| 0006 | Multiline-by-default for SO and PO | [`0006-multiline-by-default.md`](./0006-multiline-by-default.md) | **Accepted** | Always iterate `for line in lines`; no `body.lines[0]` shortcuts anywhere. |
| 0007 | Soft delete on every domain table | [`0007-soft-delete.md`](./0007-soft-delete.md) | **Accepted** | `deleted_at TIMESTAMPTZ NULL`; queries filter `.where(deleted_at.is_(None))`; purge job hard-deletes after retention. |
| 0008 | Per-tenant feature flags via Unleash | [`0008-per-tenant-feature-flags.md`](./0008-per-tenant-feature-flags.md) | **Accepted** (W0 ships a stub; W1 wires the client; full UI in W7) | Unleash with per-tenant override + percentage rollout; default `off` for new flags. |
| 0009 | OTel + Prometheus first-class on every handler | [`0009-otel-prometheus-first-class.md`](./0009-otel-prometheus-first-class.md) | **Accepted** | Auto-instrumentation + per-handler span attributes + Prometheus registry + structured JSON logs. |
| 0010 | Domain / infrastructure / workflows split per module | [`0010-domain-infrastructure-split.md`](./0010-domain-infrastructure-split.md) | **Accepted** | One Python package per SPEC module; consistent inner layout (`domain/`, `infrastructure/`, `workflows/`, `consumers/`, `outbox/`, `api.py`). |

## How to add a new ADR

1. Create `docs/adrs/NNNN-kebab-case-title.md`.
2. Use the template at the top of any existing ADR: Status, Date, Wave, Supersedes, Related; then **Context**, **Decision**, **Consequences** (positive, negative, what we lose).
3. Add a row to the table above. Order is **the order the decision was made**, not the order of ADRs by topic. New ADRs append at the bottom; never renumber.
4. If the ADR supersedes an earlier one, edit the earlier ADR's "Superseded by" field and add a footer pointing at the new one. Do not delete the superseded ADR.

## When to write an ADR

- A wave's work forces a non-obvious choice between two or more
  reasonable approaches. The choice is durable; the next contributor
  will want the reasoning.
- A constraint from outside the repo (Keycloak, Apicurio, Unleash,
  the parent repo's branch strategy) shapes the design.
- A trade-off means we *lose* something. The ADR captures what we
  gave up so a future contributor doesn't accidentally re-introduce
  it.

ADRs are written **as each wave's work forces a decision**, not all up
front. W0 ships ten because W0's plumbing decisions (the framework,
the layout, the cross-cutting concerns) are the load-bearing ones
that later waves inherit. A wave that doesn't introduce a new
architectural decision doesn't need a new ADR.
