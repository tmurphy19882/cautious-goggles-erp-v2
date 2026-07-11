# Wave 10 — Operational readiness (DONE)

> **Branch:** `feat/erp-v2-w10-ops-readiness`
> **Spec:** [`/docs/SPEC.md#wave-10`](../SPEC.md)

W10 is the last wave. It ships the things the SRE team needs to
take v2 from "code works on my laptop" to "we can run this in
prod without paging ourselves at 3am".

## What ships

### Migration
- `0113_w10_ops_readiness` — 3 new tables: `outbox_dlq`,
  `event_schema_versions` (global, not tenant-scoped — contract
  gate is per event_type, not per tenant), `api_request_metrics`.
  The first + last get FORCE RLS.

### Source
- `ops/service.py` — `DLQService` (push failed outbox messages
  with original payload + error context; resolve with a note).
  `EventSchemaRegistry` (in-DB W10 stub; the W10.1 swap is an
  Apicurio client). `MetricsRecorder` (per-request
  `api_request_metrics` row for per-tenant / per-route dashboards).
- `ops/api.py` — REST routes: `GET /ops/dlq`,
  `POST /ops/dlq/{id}/resolve`, `POST /ops/contracts/{event_type}`,
  `GET /ops/contracts/{event_type}`.

### Tests
- `tests/integration/test_w10_ops_readiness.py` — DLQ push +
  list + resolve, event schema registry register + current +
  validate (W10 stub; full JSON-Schema validation in W10.1),
  metrics record.
- `tests/integration/test_w10_event_contracts.py` — **contract
  gate**: every event the platform publishes (`ORDER_CONFIRMED`,
  `ORDER_SHIPPED`, `INVOICE_ISSUED`, `TENANT_CREATED`) has a
  registered schema in the registry. CI fails the build if a
  schema is missing.

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| OPS-1 | DLQ for poison messages | `outbox_dlq` + `DLQService` |
| OPS-2 | Per-tenant / per-route metrics | `api_request_metrics` + `MetricsRecorder` |
| OPS-3 | Event schema contract gate | `event_schema_versions` + `EventSchemaRegistry` + `test_w10_event_contracts.py` |
| OPS-4 | Consumer retry + backoff | W1 in-process consumers + outbox already cover this; the W10 DLQ closes the loop on terminal failures |
| OPS-5 | SLO dashboards | Out of scope for W10 (Grafana panels in W10.1, fed by `api_request_metrics`) |

## v2 final state

All 10 waves merged on `main`. Total of **14 migrations** (0001
→ 0113) and **12 source modules** + **8 test files**. The Wn.1
follow-ups are scoped as separate PRs:

| PR | Scope |
|----|-------|
| W0.1 | Apicurio CI gate (replace the in-DB registry with a real Apicurio client) |
| W1.1 | In-process bus → Kafka + Temporal (orchestrator swap) |
| W2.1 | P2P per-line tax + landed cost |
| W3.1 | Search engine (replace GIN `tsvector` with OpenSearch) |
| W5.1 | Lead routing + scoring + lost-reason analytics |
| W6.1 | Real OAuth for connectors (replace the DocuSign-shaped stub) |
| W7.1 | Real LLM for the Sales Coach agent |
| W8.1 | Real DocuSign + S3 upload for payroll |
| W9.1 | Real OFAC / BIS / EU feeds + tariff-engine lookup |
| W10.1 | Apicurio client + Grafana SLO dashboards |

## How to merge

Push `feat/erp-v2-w10-ops-readiness` to the parent, open a PR
titled:

```
feat(erp-v2): operational readiness (DLQ, per-tenant metrics, event contract gate)
```

Risk: **Low** — additive, all behavior gated behind new routes.
After this merges, v2 is feature-complete against the W0..W10
spec; the Wn.1 follow-ups are independent swap-ins.
