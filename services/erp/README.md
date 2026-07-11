# ERP v2

Clean rebuild of `services/erp` from the parent monorepo `cautious-goggles`.

See the **root** [`/README.md`](../../../README.md), [`/AUDIT.md`](../../../AUDIT.md),
[`/MERGE_PLAN.md`](../../../MERGE_PLAN.md), and [`/docs/SPEC.md`](../../../docs/SPEC.md)
for the full plan. This README is just the local pointer.

## Layout (target after W0)

```
services/erp/
├── src/
│   ├── app.py                         # FastAPI factory
│   ├── shared/                        # Pydantic schemas, errors, idempotency
│   ├── observability/                 # OTel, metrics, structured logging
│   ├── identity/                      # RBAC: users, roles, permissions
│   ├── master-data/                   # party, product, location, pricing
│   ├── o2c/                           # sales order, credit, invoice, payment
│   ├── p2p/                           # requisition, PO, 3-way match, AP
│   ├── finance/                       # COA, journal, period, tax, FX
│   ├── crm/                           # contact, lead, opp, quote, activity
│   ├── hr/
│   ├── legal/
│   ├── trade/                         # HTS, customs, FTZ, screening
│   ├── platform/                      # tenant onboarding, connectors, webhooks
│   ├── ai/                            # agent registry + RAG clients
│   └── api/                           # FastAPI routers
├── migrations/versions/               # Alembic
└── tests/{unit,integration,contract}/
```

## Current status

W0 not started. `src/app.py` raises `NotImplementedError` to prevent accidental boot.
