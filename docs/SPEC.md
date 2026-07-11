# ERP v2 Specification

> **Source:** `cautious-goggles` `services/erp/SPEC.md` (v1) + `docs/architecture/ERP_CRM_GAP_ANALYSIS.md` + `AUDIT.md` (this repo)
> **Target:** `services/erp` v2 in this repo
> **Status:** living document — every wave updates its section

This SPEC defines the **target** for v2. v1 was a thin O2C slice + trade compliance.
v2 is the full ERP module (Party, Product, Location, Pricing, GL, O2C, P2P, CRM,
HR, Legal, Trade, Platform) per `ADR-0016`.

## 1. Module map

```
ERP v2
├── identity/            # tenant + user + role + permission (RBAC)
├── master-data/
│   ├── party/           # customers, vendors, carriers, internal_orgs, employees
│   ├── product/         # parts, BOMs, UoM conversions
│   ├── location/        # warehouses, plants, addresses
│   └── pricing/         # price lists, contracts, discounts
├── o2c/                 # order-to-cash
│   ├── sales-order/
│   ├── credit/
│   ├── reservation/
│   ├── invoice/         # AR
│   ├── payment/         # AR cash application
│   ├── revenue-recognition/
│   └── cogs/            # FIFO layer
├── p2p/                 # procure-to-pay
│   ├── requisition/
│   ├── purchase-order/
│   ├── approval/
│   ├── receipt/         # WMS event consumer
│   ├── three-way-match/
│   └── ap/              # AP invoices, aging
├── finance/
│   ├── chart-of-accounts/
│   ├── journal/
│   ├── period/
│   ├── tax/
│   ├── fx/
│   └── aging/           # AR + AP aging jobs
├── crm/                 # lives under ERP per ADR-0016
│   ├── contact/
│   ├── lead/
│   ├── pipeline/
│   ├── opportunity/
│   ├── quote/
│   ├── contract/
│   ├── activity/
│   ├── ticket/
│   └── ai-coach/        # suggest-only, RAG over Party
├── hr/                  # employees, departments, payroll hand-off
├── legal/               # contracts, clauses, approval workflows
├── trade/               # HTS, customs, FTZ, screening (lifted from v1)
├── platform/
│   ├── tenant-onboarding/
│   ├── connector/       # OAuth handshake + sync
│   ├── webhook/         # outbound + inbound
│   └── import-export/   # CSV/XLSX
├── ai/                  # agent registry client, RAG client
├── observability/       # OTel, metrics, structured logging
└── shared/              # Pydantic schemas, errors, idempotency, outbox
```

## 2. Cross-cutting (every module must obey)

| Concern | Rule |
|---------|------|
| **Multi-tenancy** | All tenant tables: `tenant_id UUID NOT NULL`, `FORCE ROW LEVEL SECURITY`, `app.tenant_id` GUC set per request. No `erp_user` bypass in app code. |
| **Idempotency** | Every POST/PATCH requires `Idempotency-Key` header. Backed by `idempotency_keys` table. |
| **Outbox** | Every domain event written to `outbox_events` in the same transaction as the state change. Polled by `outbox_poller` and published to Kafka via `shared-events` package. |
| **Avro** | Every event payload validated against `packages/shared-events/schemas/*.avsc` via Apicurio. CI gate (`validate_manifest.py`) refuses merges with drift. |
| **RBAC** | Every mutating route calls `PermissionService.assert_can(user, action, resource)`. |
| **Observability** | Every handler: OTel span, `requests_total{tenant, route, status}` counter, `request_latency_seconds` histogram, structured log with `trace_id`, `tenant_id`, `user_id`. |
| **Soft delete** | `deleted_at TIMESTAMPTZ NULL` on every domain table. `DeletedFilter` middleware on every list endpoint. |
| **Money** | `Decimal(20, 4)` everywhere; never `float`. Currency stored as ISO 4217. |
| **Time** | `TIMESTAMPTZ` everywhere; UTC in/out. |
| **Errors** | `shared_api.errors` envelope (`code`, `message`, `details`, `trace_id`). Never raise bare `Exception`. |
| **Pydantic v2** | All schemas in `shared/schemas/`. No `dict[str, Any]` in domain code. |
| **OpenAPI** | Every route has a tag, summary, description, response model, and at least one example. `openapi.json` is published to `docs/openapi/erp.json` per CI run. |
| **Tests** | Every wave ships: happy path, auth-failure, idempotency-replay, RLS isolation. |

## 3. Wave 0 — Foundations (no behavior change, plumbing only)

**Goal:** stand up the cross-cutting infrastructure without changing the v1 behavior.

**Adds:**
- `services/erp/src/app.py` v2 factory: `create_app_v2()` mounted alongside v1.
- `services/erp/src/shared/` package: Pydantic v2 base models, error envelope, idempotency middleware wired to `DbIdempotencyStore`, RLS context manager.
- `services/erp/src/observability/`: OTel tracer init, Prometheus metrics registry, structured-logging config.
- `services/erp/src/identity/`: `User`, `Role`, `Permission`, `PermissionService` (table + service, not yet wired to every route).
- `services/erp/migrations/versions/0100_*.py`: `roles`, `permissions`, `role_permissions`, `user_roles`, `users` tables.
- `services/erp/src/api/health.py`: split `/health` (liveness) from `/ready` (readiness — checks DB + outbox + Kafka).
- `docs/openapi/erp.json` published per CI run.
- `docs/API_CHANGELOG.md` seeded.

**Doesn't change:** v1 routers, v1 models, v1 events.

**Audit items closed:** S-1, S-2 (partial), S-3 (sets the pattern), S-12, S-15, S-16, OPS-1, OPS-2, OPS-4, OPS-7, RBAC-1 (table only), RBAC-5 (table only).

## 4. Wave 1 — O2C complete

**Goal:** the O2C slice goes from "single-line happy path" to a full saga with credit, reservation, cancel, hold, invoice, payment, tax, FX, and FIFO COGS — and is wrapped in a Temporal workflow.

**Adds:**
- `o2c/sales-order/`: full Pydantic schemas, multiline validator, replace `body.lines[0]` with `for line in body.lines`.
- `o2c/credit/`: `credit_limit`, `credit_hold`, `credit_release`; `POST /orders/{id}/credit-check`; emit `CREDIT_HELD` / `CREDIT_RELEASED`.
- `o2c/reservation/`: `inventory_reservations` table, project from WMS on-hand events, release on cancel.
- `o2c/invoice/`: `invoices`, `invoice_lines`; emit `INVOICE_GENERATED`; auto-create from `SHIP_CONFIRMED` consumer.
- `o2c/payment/`: `payments`, `payment_applications`; emit `PAYMENT_RECEIVED`; partial pay, write-off, refund.
- `o2c/revenue-recognition/`: rule engine, scheduled job.
- `o2c/cogs/`: FIFO layer, `inventory_valuation` rows, post to GL on invoice.
- `o2c/workflows/order_to_cash.py`: Temporal workflow with compensation (release reservation on cancel).
- `trade/hts_engine` auto-resolve on SO line (closes TR-1, TR-2).
- `finance/tax/`: tax engine, exemption certs, nexus rules.
- `finance/fx/`: rate table, daily job, revaluation.

**Replaces:** v1 `o2c/order_flow.py` (kept as compat shim behind `erp.v2.o2c.enabled` flag until W6).

**Audit items closed:** O2C-1..15, TR-1, TR-2, FIN-3, FIN-8, FIN-9, FIN-12, S-1.

## 5. Wave 2 — P2P complete

**Goal:** requisition → PO → approval → receipt (WMS event) → 3-way match → AP, in a Temporal workflow.

**Adds:**
- `p2p/requisition/`: `purchase_requisitions`; consumer for MRP `PURCHASE_REQUISITION_CREATED`.
- `p2p/purchase-order/`: `purchase_orders`, `purchase_order_lines`; emit `PO_CREATED` + `PO_APPROVED`.
- `p2p/approval/`: threshold rules, approver assignment, SoD (RBAC-3).
- `p2p/receipt/`: consumer for WMS `GOODS_RECEIVED`; `goods_receipts` table.
- `p2p/three-way-match/`: PO ↔ receipt ↔ supplier invoice, tolerance rules, hold on mismatch.
- `p2p/ap/`: `ap_invoices`, `ap_payments`; emit `AP_POSTED`.
- `p2p/workflows/procure_to_pay.py`: Temporal workflow.
- QMS `SUPPLIER_SUSPENDED` consumer; supplier block list on PO create.

**Audit items closed:** P2P-1..9, RBAC-3, S-1 (second saga).

## 6. Wave 3 — Master data

**Adds:**
- `master-data/product/`: `products`, `boms`, `bom_lines`, `uom_conversions`. Resolves `products` vs `item_master` (F1). MRP reads via events.
- `master-data/location/`: `warehouses`, `plants`, `addresses`. WMS reads location master.
- `master-data/pricing/`: `price_lists`, `price_list_items`, `contracts`, `discounts`.
- `master-data/party/`: extend Party with `employee`, `internal_org`; soft-delete; merge tool; full address book; tax ID; contact linkage.
- `master-data/search/`: `SearchService` over Party / Product / SO / PO.
- `master-data/import/`: `ImportService` for CSV/XLSX.
- Emit `PARTY_UPDATED`, `PRICE_UPDATED`, `PRODUCT_UPDATED`.

**Audit items closed:** MD-1..11, S-7, S-8.

## 7. Wave 4 — Finance / GL

**Adds:**
- `finance/chart-of-accounts/`: `gl_accounts`, seeded per tenant on onboarding.
- `finance/journal/`: `journal_entries`, `journal_lines`; manual entry API; auto-post from SO/PO/AR/AP events.
- `finance/period/`: `accounting_periods`, period close, lock.
- `finance/aging/`: AR/AP aging jobs.
- `finance/fx/`: revaluation job.
- `finance/budgets/`: budgets vs actuals.

**Audit items closed:** FIN-1..14, FIN-11, FIN-13.

## 8. Wave 5 — CRM core

**Adds:**
- `crm/contact/`: contacts sub-entity of Party.
- `crm/lead/`: leads, qualification, conversion to opportunity.
- `crm/pipeline/`: pipelines, stages (kanban).
- `crm/opportunity/`: opportunities, stage transitions.
- `crm/quote/`: quotes, lines, `POST /quotes/{id}/convert-to-so`.
- `crm/contract/`: contracts, clauses.
- `crm/activity/`: activity timeline on Party 360°.
- `crm/ticket/`: support tickets, comments, SLA.
- `crm/api/party_360.py`: aggregate view.
- Frontend pages: `/dashboard/leads`, `/opportunities`, `/quotes`, `/contracts`, `/tickets`, `/activities`.

**Audit items closed:** CRM-1..9.

## 9. Wave 6 — Platform / integrations

**Adds:**
- `platform/tenant-onboarding/`: real `POST /api/v1/platform/tenants` — Keycloak realm + DB provisioning + `tenant-created` outbox + WMS/TMS/QMS/MRP consumers.
- `platform/connector/`: real OAuth handshake, token storage in vault, sync jobs, `last_sync_at` writer.
- `platform/webhook/`: outbound delivery (signed, retried), inbound receiver.
- `platform/import-export/`: CSV/XLSX.
- `platform/payment-gateway/`: Stripe + ACH connector.
- `identity/api/`: roles UI, permissions matrix.
- Frontend `/settings/roles`, `/settings/permissions`.

**Audit items closed:** PL-1..9, RBAC-4, FIN-13 (cards-on-file hook).

**First wave where Kong shifts default traffic** from `:8010` to `:8001` for affected routes.

## 10. Wave 7 — CRM AI + notifications

**Adds:**
- `crm/ai-coach/`: Sales Coach agent, suggest-only, RAG over Party + orders + tickets.
- `crm/notification/`: `NotificationService` (in-app, email, SMS adapters).
- `crm/saved-view/`: per-role saved views.
- `crm/custom-field/`: per-tenant schema extension.
- `ai/registry/`: in-service agent registry client.
- `ai/rag/`: `document_chunks`, `embeddings`, vector store.

**Audit items closed:** CRM-10..12, AI-2, AI-4, AI-5, S-9, OPS-3, OPS-5, S-6.

## 11. Wave 8 — HR / Legal

**Adds:**
- `hr/`: `employees`, `departments`, `org_units`, manager hierarchy, payroll export.
- `legal/`: `contracts`, `contract_clauses`, `approval_workflows` (lifted from CRM contracts), DocuSign hook.

**Audit items closed:** HR-1..3, LGL-1..3.

## 12. Wave 9 — Trade polish

**Adds:**
- `trade/hts/`: auto-resolve on every SO line (already in W1, hardened here).
- `trade/ftz/`: `REMOVE_FROM_FTZ` action; FTZ → GL deferred-duty auto-post.
- `trade/screening/`: re-screen on any Party field change; `employee`/`internal_org` also screened.
- `trade/customs/`: state machine beyond `draft`/`filed` (`pending_ftz`, `released`).

**Audit items closed:** TR-3..9.

## 13. Wave 10 — Operational readiness

**Adds:**
- Apicurio schema-registry CI gate (already a check; this wave wires it as a hard fail).
- `tests/contract/` — pact tests against WMS / TMS / QMS / MRP.
- Per-tenant Prometheus labels.
- Outbox DLQ + alerting.
- `events_raw` table for at-least-once observability.
- Chaos tests for Kafka + Postgres failover.

**Audit items closed:** S-4, S-7..8, S-17, S-18, S-20, OPS-6.

## 14. Out of scope (intentionally)

- APS / DDMRP — lives in MRP, not ERP.
- Customer self-service portal — Phase 3+ peer parity.
- EDI inbound parsers — `apps/edi-gateway/` already a stub; outside ERP.
- Full Temporal-vs-in-process saga migration — Temporal introduced in W1/W2; legacy in-process paths retire in W10.
- External benchmark pass (parallel-cli) — pending; fold in once available.
