# cautious-goggles-erp-v2

> **ERP service rebuild — to be merged back into `cautious-goggles` (parent) on `feat/erp-crm`.**

This repo is a **clean redo of `services/erp/`** from the parent monorepo
[`cautious-goggles`](../cautious-goggles). It exists so the rebuild can be
worked on independently, reviewed in isolation, and merged back without
disrupting the main branch train.

```
cautious-goggles-erp-v2/                 (this repo — work happens here)
└── services/erp/                        # v2 service (target)
    ├── src/
    │   ├── api/                         # FastAPI routers
    │   ├── domain/                      # entities, value objects, services
    │   ├── infrastructure/              # DB, outbox, idempotency, OTel
    │   ├── workflows/                   # Temporal sagas (O2C, P2P)
    │   └── consumers/                   # Kafka consumers (real, not stubs)
    ├── migrations/                      # Alembic
    └── tests/

cautious-goggles/                        (parent — unchanged for now)
└── services/erp/                        # v1 (thin O2C slice) — receives the merge
```

## Why this repo exists

`cautious-goggles` `services/erp` is at **~15%** of SPEC scope (see [`AUDIT.md`](./AUDIT.md)).
Rebuilding it in-tree would block the merge train for weeks and conflict
with the `feat/erp-crm` work in flight. Doing it in this isolated repo lets
us:

- Run our own CI / lint / test cadence.
- Re-design the directory layout (domain / infrastructure / workflows split).
- Land big-bang rewrites (multiline orders, RBAC, GL, Temporal) behind
  feature flags without breaking `:8010` (the legacy monolith the frontend
  currently uses).
- Cherry-pick merge into `feat/erp-crm` wave-by-wave (see [`MERGE_PLAN.md`](./MERGE_PLAN.md)).

## Status

| Wave | Theme | Status |
|------|-------|--------|
| W0   | Foundations (schemas, RBAC, OTel, idempotency) | not started |
| W1   | O2C complete (multiline → invoice → payment → GL) | not started |
| W2   | P2P complete (requisition → PO → 3-way → AP) | not started |
| W3   | Master data (products, BOMs, locations, price lists) | not started |
| W4   | Finance / GL (COA, journals, period close, FX, tax) | not started |
| W5   | CRM core (contacts, leads, opps, pipeline, quotes) | not started |
| W6   | Platform / integrations (tenant onboarding, OAuth, webhooks) | not started |
| W7   | CRM AI + notifications (Sales Coach, inbox, saved views) | not started |
| W8   | HR / Legal modules (employees, contracts, approvals) | not started |
| W9   | Trade polish (HTS auto-resolve, FTZ removal, re-screen) | not started |
| W10  | Operational readiness (Avro gate, contract tests, DLQ) | not started |

The detailed wave-by-wave plan lives in [`MERGE_PLAN.md`](./MERGE_PLAN.md). Every gap
that drives the plan is documented with source and severity in [`AUDIT.md`](./AUDIT.md).

## Repo rules

1. **Trunk-based:** every wave lands on a short-lived `feat/erp-v2-wN-*` branch off
   `main` of *this* repo, then merges into *this* `main`.
2. **Mirror to parent:** the parent `cautious-goggles` `feat/erp-crm` branch gets a
   wave-by-wave PR (one wave = one PR). The parent `services/erp/` v1 is
   removed in a final cleanup PR after v2 reaches parity on a given wave.
3. **No silent API drift:** every change to an HTTP contract publishes an
   entry to [`docs/API_CHANGELOG.md`](./docs/API_CHANGELOG.md) (created in W0).
4. **Every mutating route gates on `PermissionService`:** no exceptions.
5. **Outbox + Apicurio Avro + OTel + idempotency are first-class.**
6. **No `customer_name` text on `sales_orders` — always `party_id` FK.**
7. **No hardcoded `body.lines[0]` — multiline is the only mode.**
8. **Pydantic v2 schemas everywhere; no `dict[str, Any]` in domain code.**

## Local dev (placeholder — wired in W0)

```bash
git clone https://github.com/tmurphy19882/cautious-goggles-erp-v2.git
cd cautious-goggles-erp-v2
docker compose -f infra/docker-compose.yml up -d
cd services/erp
alembic upgrade head
uvicorn src.app:app --reload --port 8001
pytest -q
```

The infra file, Dockerfile, and pyproject are stubs until W0 lands.

## Related

- **Parent repo:** `cautious-goggles` (private, same owner)
- **Audit:** [`AUDIT.md`](./AUDIT.md) — exhaustive list of what v1 *can't* do
- **v2 spec:** [`docs/SPEC.md`](./docs/SPEC.md) — what v2 *will* do
- **Merge plan:** [`MERGE_PLAN.md`](./MERGE_PLAN.md) — how this folds back into the parent
