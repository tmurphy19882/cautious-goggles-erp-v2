# ERP v2 — Delivery Complete (W0..W10)

> **Status:** All 10 waves merged to `main`.
> **Total migrations:** 14 (0001 → 0113).
> **Total source modules:** 12 (api, observability, shared, identity,
> master_data, o2c, p2p, finance, crm, platform, hr, trade, ops).
> **Total test files:** 8 integration suites covering every module.

## Wave-by-wave

| Wave | Branch | Scope | Migrations | Source | Tests |
|------|--------|-------|------------|--------|-------|
| W0 | `feat/erp-v2-w0-foundations` | Foundations: bug audit, infra, docs, smoke test | 0001..0100 | api, observability, shared, identity | smoke + unit |
| W1 | `feat/erp-v2-w1-o2c-complete` | O2C complete (multiline, credit, reservation, FIFO COGS) | 0101 | o2c | test_w1_o2c |
| W2 | `feat/erp-v2-w2-p2p-complete` | P2P complete (requisition, PO, 3-way, AP) | 0102 | p2p | test_w2_p2p |
| W3+W4 | `feat/erp-v2-w3-w4-master-finance` | Master data + finance/GL | 0103, 0104, 0105 | master_data, finance | test_w3_w4 |
| W5 | `feat/erp-v2-w5-crm-core` | CRM core (leads, opportunities, pipeline, quotes → SO) | 0106, 0107, 0108 | crm | test_w5_crm |
| W6 | `feat/erp-v2-w6-platform` | Platform (tenant onboarding, webhooks, payments, RBAC) | 0109 | platform | test_w6_platform |
| W7 | `feat/erp-v2-w7-crm-ai` | CRM AI + notifications + custom fields + saved views | 0110 | crm | test_w7_crm_ai |
| W8 | `feat/erp-v2-w8-hr-legal` | HR + Legal (employees, departments, contracts, e-sign) | 0111 | hr | test_w8_hr_legal |
| W9 | `feat/erp-v2-w9-trade-polish` | Trade polish (HTS auto-resolve, FTZ, screening) | 0112 | trade | test_w9_trade_polish |
| W10 | `feat/erp-v2-w10-ops-readiness` | Ops (DLQ, per-tenant metrics, event contract gate) | 0113 | ops | test_w10_ops_readiness + test_w10_event_contracts |

## Per-wave docs

- [`W0_FOUNDATIONS.md`](./waves/WAVE_0_FOUNDATIONS.md)
- [`W1_O2C.md`](./waves/WAVE_1_O2C.md)
- [`W2_P2P.md`](./waves/WAVE_2_P2P.md)
- [`W3_W4_MASTER_FINANCE.md`](./waves/WAVE_3_W4_MASTER_FINANCE.md)
- [`W5_CRM_CORE.md`](./waves/WAVE_5_CRM_CORE.md)
- [`W6_PLATFORM.md`](./waves/WAVE_6_PLATFORM.md)
- [`W7_CRM_AI.md`](./waves/WAVE_7_CRM_AI.md)
- [`W8_HR_LEGAL.md`](./waves/WAVE_8_HR_LEGAL.md)
- [`W9_TRADE_POLISH.md`](./waves/WAVE_9_TRADE_POLISH.md)
- [`W10_OPS_READINESS.md`](./waves/WAVE_10_OPS_READINESS.md)

## Audit items closed

| Count | Bucket |
|-------|--------|
| 5 | P0 (W0 bug audit) |
| 1 | O2C (O2C-15 quotes → SO) |
| 1 | P2P (P2P-10 supplier-suspend block) |
| 5 | CRM (CRM-1..CRM-12 except CRM-12 which is W7.1) |
| 8 | Platform (PL-1..PL-5, PL-7, PL-8, RBAC-4) |
| 5 | CRM-2 (CRM-7..CRM-11) |
| 7 | HR + Legal (HR-1..HR-3, LEG-1..LEG-4) |
| 4 | Trade (TR-1..TR-4) |
| 4 | Ops (OPS-1..OPS-4) |

## Architecture summary

```
┌─────────────── ERP v2 ────────────────┐
│  FastAPI app (api/main.py)             │
│  - ObservabilityMiddleware             │
│  - IdempotencyMiddleware                │
│  - per-module routers                   │
│                                         │
│  modules:                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │  o2c    │ │  p2p    │ │ finance │  │
│  │ (W1)    │ │ (W2)    │ │ (W4)    │  │
│  └────┬────┘ └────┬────┘ └────┬────┘  │
│       │           │           │        │
│  ┌────┴───────────┴───────────┴────┐   │
│  │      shared.db / errors /       │   │
│  │      idempotency / schemas      │   │
│  └─────────────────────────────────┘   │
│       │           │           │        │
│  ┌────┴────┐ ┌────┴────┐ ┌────┴────┐  │
│  │ master  │ │  crm    │ │  hr /   │  │
│  │  data   │ │ (W5+7)  │ │ trade / │  │
│  │  (W3)   │ │         │ │ platform│  │
│  └─────────┘ └─────────┘ │  / ops  │  │
│                          │(W6+8+9+10)│
│                          └──────────┘  │
└─────────────────────────────────────────┘
           │                       │
           ▼                       ▼
    PostgreSQL 16            Kafka 3.6 (W1.1)
    (RDS / Aurora)          (W1.1 — in-process
                            EventBus for now)
```

## Multi-tenancy

- Every tenant-scoped table has `tenant_id UUID NOT NULL`.
- RLS is **enabled + forced** on every tenant-scoped table.
- The middleware sets `app.tenant_id` GUC per request; the
  policy compares `tenant_id` to that GUC via the
  `COALESCE(NULLIF(current_setting(...), ''), SENTINEL)` pattern.
- The `tenants` table is global (not tenant-scoped — it IS the
  tenant).
- The `event_schema_versions` table is global (contract gate is
  per event_type, not per tenant).

## Idempotency

- `IdempotencyMiddleware` enforces `Idempotency-Key` on every
  mutating request.
- Replay returns the original response body + status.
- Mismatched body on a replay returns 409.
- Hash: `sha256(f"{tenant_id}:{key}")` per v1 (ADOPT-1).

## Outbox

- Every event publish writes to `outbox` first, then an in-process
  relay pushes to the bus.
- W1 ships the in-process bus + consumers; W1.1 swaps for Kafka.
- W10 ships the DLQ + contract gate as the operational safety net.

## What's NOT in v2 (Wn.1 follow-ups)

These are tracked as separate PRs; v2 is feature-complete without
them. None block the W0..W10 happy paths.

| PR | Scope |
|----|-------|
| W0.1 | Apicurio CI gate (real client) |
| W1.1 | Kafka + Temporal (orchestrator swap) |
| W2.1 | P2P per-line tax + landed cost |
| W3.1 | OpenSearch (replace GIN tsvector) |
| W5.1 | Lead routing + scoring + analytics |
| W6.1 | Real OAuth for connectors |
| W7.1 | Real LLM for Sales Coach |
| W8.1 | Real DocuSign + S3 upload |
| W9.1 | Real OFAC / BIS / EU feeds |
| W10.1 | Grafana SLO dashboards |
