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

## Quick start (5 min)

A new contributor can go from `git clone` to `curl /health` in
about five minutes. The full guide is in
[`docs/GETTING_STARTED.md`](./docs/GETTING_STARTED.md).

```bash
git clone https://github.com/tmurphy19882/cautious-goggles-erp-v2.git
cd cautious-goggles-erp-v2
docker run --name erp-pg -d -e POSTGRES_USER=erp -e POSTGRES_PASSWORD=erp \
  -e POSTGRES_DB=erp_db -p 5432:5432 postgres:16
cd services/erp
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
export ERP_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_db
alembic upgrade head
uvicorn src.app:app --reload --port 8001
# in another shell:
curl http://localhost:8001/health
```

If anything in the above breaks, jump to
[`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md).

## Local dev

The day-to-day loop — branches, module layout, how to add a
module / migration / permission, the merge-back flow, the
coverage guard — is in
[`docs/DEVELOPMENT.md`](./docs/DEVELOPMENT.md).

```bash
# Before you write your first line of code:
cat docs/DEVELOPMENT.md
# Quick links inside:
#   - Branch naming (feat/erp-v2-wN-*)
#   - Where the code lives (services/erp/src/{shared,observability,identity,api,...})
#   - How to add a new module
#   - How to add a new migration (alembic revision, naming, RLS convention)
#   - How to add a new permission (catalog seed in 0100 + per-tenant grant in W6)
#   - The merge-back flow (parent PR per wave)
#   - The coverage guard (erp-v2-check-coverage)
```

## Troubleshooting

Most "is this a bug" questions are answered in
[`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md). Common
failures covered:

- `permission denied for table users` — running migrations without
  the right role.
- `RLS policy violation` — `app.tenant_id` GUC not set.
- `Idempotency-Key required` — forgot the header on a POST.
- `idempotency_keys relation does not exist` — migration not run.
- `OTLP endpoint unreachable` — observability is not strictly
  required; can be disabled.
- Tests skip with `Postgres not available` — set
  `ERP_TEST_DATABASE_URL`.
- `ImportError: cannot import name 'X' from 'shared'` — import
  rules in `shared/__init__.py`.

## Wave status

| Wave | Theme | Status | Doc |
|------|-------|--------|-----|
| W0   | Foundations (schemas, RBAC, OTel, idempotency) | **done** | [`docs/waves/WAVE_0_FOUNDATIONS.md`](./docs/waves/WAVE_0_FOUNDATIONS.md) |
| W1   | O2C complete (multiline → invoice → payment → GL) | planned | [`docs/SPEC.md#wave-1`](./docs/SPEC.md#4-wave-1--o2c-complete) |
| W2   | P2P complete (requisition → PO → 3-way → AP) | planned | [`docs/SPEC.md#wave-2`](./docs/SPEC.md#5-wave-2--p2p-complete) |
| W3   | Master data (products, BOMs, locations, price lists) | planned | [`docs/SPEC.md#wave-3`](./docs/SPEC.md#6-wave-3--master-data) |
| W4   | Finance / GL (COA, journals, period close, FX, tax) | planned | [`docs/SPEC.md#wave-4`](./docs/SPEC.md#7-wave-4--finance--gl) |
| W5   | CRM core (contacts, leads, opps, pipeline, quotes) | planned | [`docs/SPEC.md#wave-5`](./docs/SPEC.md#8-wave-5--crm-core) |
| W6   | Platform / integrations (tenant onboarding, OAuth, webhooks) | planned | [`docs/SPEC.md#wave-6`](./docs/SPEC.md#9-wave-6--platform--integrations) |
| W7   | CRM AI + notifications (Sales Coach, inbox, saved views) | planned | [`docs/SPEC.md#wave-7`](./docs/SPEC.md#10-wave-7--crm-ai--notifications) |
| W8   | HR / Legal modules (employees, contracts, approvals) | planned | [`docs/SPEC.md#wave-8`](./docs/SPEC.md#11-wave-8--hr--legal) |
| W9   | Trade polish (HTS auto-resolve, FTZ removal, re-screen) | planned | [`docs/SPEC.md#wave-9`](./docs/SPEC.md#12-wave-9--trade-polish) |
| W10  | Operational readiness (Avro gate, contract tests, DLQ) | planned | [`docs/SPEC.md#wave-10`](./docs/SPEC.md#13-wave-10--operational-readiness) |

W0 ships the cross-cutting infrastructure only — no business
behaviour. The first behaviour is W1 (O2C complete, with
multiline, credit, reservation, cancel, hold, invoice, payment,
FIFO COGS, tax, FX, wrapped in a Temporal workflow). The
detailed wave-by-wave plan lives in
[`MERGE_PLAN.md`](./MERGE_PLAN.md). Every gap that drives the
plan is documented with source and severity in
[`AUDIT.md`](./AUDIT.md).

A short summary per wave lives in [`docs/waves/`](./docs/waves/);
the full SPEC per wave is in [`docs/SPEC.md`](./docs/SPEC.md);
the architectural decisions behind the wave's choices are in
[`docs/adrs/`](./docs/adrs/); the module tree is in
[`docs/trees/`](./docs/trees/).

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

## Related

- **Parent repo:** `cautious-goggles` (private, same owner)
- **Audit:** [`AUDIT.md`](./AUDIT.md) — exhaustive list of what v1 *can't* do
- **v2 spec:** [`docs/SPEC.md`](./docs/SPEC.md) — what v2 *will* do
- **Merge plan:** [`MERGE_PLAN.md`](./MERGE_PLAN.md) — how this folds back into the parent
- **ADRs:** [`docs/adrs/`](./docs/adrs/) — the 10 architectural decisions that shape v2
- **Trees:** [`docs/trees/`](./docs/trees/) — module map (ERP, ERP-CRM)
- **Getting started:** [`docs/GETTING_STARTED.md`](./docs/GETTING_STARTED.md) — 5-min quick start
- **Development:** [`docs/DEVELOPMENT.md`](./docs/DEVELOPMENT.md) — day-to-day dev loop
- **Troubleshooting:** [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md) — common failures and fixes
- **API changelog:** [`docs/API_CHANGELOG.md`](./docs/API_CHANGELOG.md) — every additive / breaking HTTP change
