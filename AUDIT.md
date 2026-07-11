# ERP Gap Audit — Actions the current ERP *cannot* perform

> **Source repo:** `cautious-goggles` (parent) — branch of record `feat/erp-crm` from `main@6cecdec`
> **Service under audit:** `services/erp/` (FastAPI, SQLAlchemy 2.0 async, Postgres + RLS, Kafka via outbox)
> **Audit date:** 2026-07-11
> **Method:** read every file in `services/erp/src/`, cross-checked against `SPEC.md`, `CONTEXT.md`, `docs/architecture/ERP_CRM_GAP_ANALYSIS.md`, `docs/architecture/BUILD_STATUS.md`, `docs/architecture/DEPLOY_READINESS_GAP.md`, and the `trees/erp.md` / `trees/erp-crm.md` trees.

This document answers one question, exhaustively:

> **"What ERP business actions can a tenant *not* perform through the current `services/erp` service, even though the platform spec says they should?"**

Every item is sourced. Severity = **P0** (blocks MVP), **P1** (blocks sellable), **P2** (maturity).

---

## 1. Headline numbers

| Metric | Today | Target (per SPEC + peer scan) |
|---|---:|---:|
| Models defined in `services/erp/src/models.py` (domain tables) | **2** (`SalesOrder`, `SalesOrderLine`) | ~25+ (Party, Product, Location, Pricing, GL, PO, Invoice, Payment, InventoryVal, HR, Legal, CRM, Trade) |
| Routers mounted in `app.py` | **4** (`orders`, `parties`, `trade`, `connectors`) | ~10+ (`products`, `locations`, `pricing`, `gl`, `purchase-orders`, `invoices`, `payments`, `inventory-valuation`, `crm/*`, `hr/*`, `legal/*`) |
| Endpoints reachable by a tenant (auth'd) | **~14** | ~120+ |
| Endpoints with backing DB model | **~8** | ~120+ |
| In-process event consumers actually wired to a queue | **0** (only in-process pub/sub) | ≥3 (PURCHASE_REQUISITION_CREATED, SHIP_CONFIRMED, SUPPLIER_SUSPENDED) |
| Cross-service event loops proven live | **0** | ≥1 (ORDER_CONFIRMED → WMS → INVOICE_GENERATED) |
| Module ACL / RBAC enforcement on mutations | **0** | Every mutation gated by `PermissionService` |
| Multiline sales order support | **Hardcoded to 1 line** (`body.lines[0]`) | Unlimited lines, with validation, tax, discount |
| Real credit check | **No-op** (TODO) | Hold/release with exposure cap |
| Real reservation | **No-op** | Reserve against inventory projection, release on cancel |
| Invoicing (AR) | **Missing** | `INVOICE_GENERATED` event + GL journal post |
| Payment application | **Missing** | AR aging, partial payments, write-off, refund |
| Procurement (P2P) end-to-end | **Missing** | Requisition → PO → receipt (WMS event) → 3-way match → AP |
| GL journal posting | **Missing** | Auto-post from SO, PO, AP, AR; manual journal entry |
| Inventory valuation | **Missing** | FIFO COGS snapshot from WMS on-hand events |
| CRM pipeline (Leads → Opp → Quote → Contract) | **Missing** | Full funnel with kanban, activity timeline, AI coach |
| Tickets / Support | **Missing** | SLA, comments, assignment, status |
| Tasks / Calendar | **Missing** | Per-user, per-record |
| Notifications center | **Missing** | In-app + email/SMS adapters |
| Saved views / bulk / import-export | **Missing** | Per-role views, CSV/XLSX, scheduled |
| Custom fields | **Missing** | Per-tenant schema extension |
| RBAC UI | **Missing** | Role/permission matrix in `/settings/roles` |
| Finance close | **Missing** | Period close, accruals, FX revaluation |
| HR module (employees, departments, org) | **Stub (README only)** | Full CRUD + payroll hand-off |
| Legal module (contracts, approvals) | **Stub (README only)** | Full CRUD + e-sign hook |
| CRM AI Coach / RAG | **Missing** | Suggest-only agent over Party + orders + tickets |
| Approval workflow engine | **Missing** | Per-module approval with HITL queue |
| Temporal sagas (`OrderToCashWorkflow`, `ProcureToPayWorkflow`) | **Missing** (SPEC requires them) | Both wired to live workflows |

**Honest score (this audit, ERP only):** **~15%** of SPEC scope implemented, **~28%** of legacy-monolith capability ported. Source: `BUILD_STATUS.md` (45% claimed) is generous because it credits the legacy monolith as "shipped" — for the `services/erp` microservice itself, the real number is closer to **15%**.

---

## 2. Catalog: actions that cannot be performed

Each item: **what you can't do → why → severity → where the gap lives.**

### 2.1 Order-to-Cash (O2C) — partial

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| O2C-1 | **Create a multi-line sales order** | `src/api/orders.py:37` — `line = body.lines[0]` hardcodes the first line; all others are silently dropped | **P0** | `src/api/orders.py` |
| O2C-2 | **Run a real credit check** | No `credit_check()` exists; SPEC requires it before reservation. Status field defaults to `"confirmed"` with no gate | **P0** | `src/o2c/order_flow.py:114-122`; `SPEC.md` O2C list |
| O2C-3 | **Reserve inventory** | No `inventory_reservation` table, no projection logic. WMS owns physical, ERP doesn't even read WMS on-hand events to compute a reservation | **P0** | `src/models.py` — no reservations table |
| O2C-4 | **Cancel / amend a sales order** | `SalesOrder.status` is write-once; no PATCH endpoint, no cancel outbox event, no release-of-reservation flow | **P0** | `src/api/orders.py` (no PATCH) |
| O2C-5 | **Hold a sales order (QMS hook)** | SPEC says QMS `HOLD_PLACED` blocks shipment / pauses O2C. No consumer, no hold state | **P0** | `README.md` "Events consumed" + `src/consumers/register.py` (only stubs) |
| O2C-6 | **Generate an invoice (AR)** | No `invoices` table, no `INVOICE_GENERATED` event, no GL post. Cannot close O2C | **P0** | `src/models.py`; `BUILD_STATUS.md` next-3 targets |
| O2C-7 | **Receive a payment** | No `payments` table, no `PAYMENT_RECEIVED` event, no AR aging | **P0** | `SPEC.md` events published; `models.py` |
| O2C-8 | **Recognize revenue** | No event, no scheduled job, no recognition rule engine | **P1** | `SPEC.md` "O2C: revenue recognition" |
| O2C-9 | **Compute FIFO COGS** | No `inventory_valuation` table, no FIFO layer logic, no on-hand snapshot from WMS | **P1** | `SPEC.md` "FIFO COGS" |
| O2C-10 | **Run the O2C workflow as a Temporal saga** | SPEC: `OrderToCashWorkflow` (Temporal). Current: a single FastAPI handler. No saga, no retries, no compensation | **P1** | `SPEC.md` Workflows; no `temporal/` dir in `services/erp/` |
| O2C-11 | **Price an order from a price list** | `CreateOrderRequest` accepts a literal `unit_price` per line; no price-list lookup, no contract pricing, no tier discount | **P1** | `src/api/orders.py:38`; no `price_lists` table |
| O2C-12 | **Tax the order (multi-jurisdiction)** | No tax engine, no nexus, no exemption certificates, no Avalara/TaxJar hook | **P1** | ERP_CRM_GAP_ANALYSIS § 3 "GL / tax / multi-currency" |
| O2C-13 | **Currency-convert the order** | `currency` is stored but never converted; no FX rate table, no revaluation, no MT quoting | **P1** | `SalesOrder.currency` (string only) |
| O2C-14 | **Emit `PO_APPROVED` / `INVOICE_GENERATED` / `PAYMENT_RECEIVED`** | Only `ORDER_CONFIRMED` (`scm.erp.order-confirmed.v1`) is published. The other three SPEC-required topics have no producer | **P0** | `SPEC.md` events published; `src/o2c/order_flow.py:152` |
| O2C-15 | **Quote → convert-to-SO** | No `quotes` table, no `Quote` API, no conversion | **P0** | `ERP_CRM_GAP_ANALYSIS § 11` |

### 2.2 Procure-to-Pay (P2P) — entirely missing in service

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| P2P-1 | **Create a purchase requisition** | No `purchase_requisitions` table, no API, no consumer for `PURCHASE_REQUISITION_CREATED` from MRP | **P0** | `SPEC.md` "P2P: requisitions"; `src/consumers/` (no MRP consumer) |
| P2P-2 | **Create a purchase order** | No `purchase_orders` table in `models.py`. Explicitly noted in `CONTEXT.md`: *"purchase_orders is not yet implemented in services/erp/src/models.py"* | **P0** | `CONTEXT.md` "Open questions" + `src/models.py` |
| P2P-3 | **Approve a PO** | No `po_approvals` workflow, no threshold logic, no approver assignment | **P0** | `SPEC.md` "PO_APPROVED" event needs an actor |
| P2P-4 | **Receive goods (event-driven from WMS)** | No consumer for WMS `GOODS_RECEIVED`, no `goods_receipts` table | **P0** | `src/consumers/register.py` only handles `ORDER_CONFIRMED` |
| P2P-5 | **Run a 3-way match** | No `three_way_match` table, no tolerance rules, no mismatch hold | **P0** | `CONTEXT.md` "not yet a named table/event" |
| P2P-6 | **Post AP (supplier invoice)** | No `ap_invoices` table, no GL post, no AP aging | **P0** | `SPEC.md` "P2P: AP" |
| P2P-7 | **Generate `PO_APPROVED` event** | Producer missing — see O2C-14 | **P0** | `SPEC.md` events |
| P2P-8 | **Suspend a supplier on QMS `SUPPLIER_SUSPENDED`** | No consumer, no supplier-block list on PO create | **P0** | `SPEC.md` events consumed; `src/consumers/` |
| P2P-9 | **Run P2P as a Temporal saga** | Same gap as O2C-10 | **P1** | `SPEC.md` Workflows |
| P2P-10 | **Send a PO to a supplier (EDI 850 / email / portal)** | No outbound PO transport, no EDI 850 mapping | **P2** | `EDI_TRANSACTION_CATALOG.md` |

### 2.3 Master data — mostly stubbed in service, real data still in legacy monolith

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| MD-1 | **CRUD products / parts in ERP service** | No `products` / `parts` table in `models.py`. Naming conflict between SPEC (`products`) and `DATA_MODEL.md` (`item_master`) — flagged in `CONTEXT.md` Finding F1 | **P0** | `src/models.py`; `CONTEXT.md` "Open questions" |
| MD-2 | **CRUD Bill-of-Materials (BOMs) in ERP service** | No `boms` / `bom_lines` table | **P1** | `README.md` "Data owned by ERP" (BOM owned here) |
| MD-3 | **CRUD UoM conversions in ERP service** | No `uom_conversions` table | **P1** | `README.md` |
| MD-4 | **CRUD locations (warehouses, plants, addresses) in ERP service** | No `warehouses` / `plants` / `addresses` table — WMS cannot read location master from a non-existent service | **P0** | `README.md` "Location: WMS reads location master from ERP" |
| MD-5 | **CRUD price lists / price list items** | No `price_lists` / `price_list_items` table | **P1** | `README.md` |
| MD-6 | **Party master in service is partial** | `parties` API exists (`src/api/parties.py`) but **only** for trade-screening types `customer|vendor|carrier`. No `employee`, no `internal_org`, no `contact` sub-entity, no address book | **P0** | `src/api/parties.py:29` regex |
| MD-7 | **Update party name / address from the service** | `PATCH /parties/{id}` exists but only patches `name`, `country_code`, `external_ref` — no address, no tax ID, no contact linkage | **P1** | `src/api/parties.py:80` |
| MD-8 | **Soft-delete or merge parties** | No soft-delete column on parties, no merge/golden-record tool | **P1** | `models.py` (no `deleted_at`) |
| MD-9 | **Bulk import products / parties / price lists** | No `ImportService`, no CSV/XLSX endpoint | **P1** | `ERP_CRM_GAP_ANALYSIS § 13` |
| MD-10 | **Search products / parties globally** | No `SearchService`, no `tsvector` index, no fuzzy | **P1** | `ERP_CRM_GAP_ANALYSIS § 5` |
| MD-11 | **Emit `PARTY_UPDATED` / `PRICE_UPDATED`** | Events listed in README don't have producers | **P1** | `README.md` events published |

### 2.4 Finance / GL — missing

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| FIN-1 | **Maintain a chart of accounts** | No `gl_accounts` table | **P0** | `SPEC.md` "GL accounts" key table |
| FIN-2 | **Post a manual journal entry** | No `journal_entries` / `journal_lines` table, no API | **P0** | `SPEC.md` GL |
| FIN-3 | **Auto-post journals from SO / PO / AR / AP** | No posting engine; no event consumers for the lines that should drive posts | **P0** | `SPEC.md` GL "valuation" |
| FIN-4 | **Run a period close** | No `accounting_periods` table, no close job, no lock | **P0** | `ERP_CRM_GAP_ANALYSIS § 3` P0 "Finance gaps blocking O2C close" |
| FIN-5 | **Compute AR aging** | No `ar_aging` view or job | **P1** | `README.md` "Data owned" |
| FIN-6 | **Compute AP aging** | No `ap_aging` view or job | **P1** | `README.md` |
| FIN-7 | **Manage budgets** | No `budgets` table | **P2** | `README.md` |
| FIN-8 | **Tax calculation (sales/use/VAT/GST)** | No tax engine, no rate tables, no exemption certs | **P0** | `ERP_CRM_GAP_ANALYSIS § 3` "GL / tax / multi-currency" P0 |
| FIN-9 | **Multi-currency revaluation** | `currency` is just a string; no FX rate table, no reval job | **P1** | `SalesOrder.currency` |
| FIN-10 | **Cost-center allocation** | No `cost_centers` table | **P2** | `README.md` |
| FIN-11 | **Bank reconciliation** | No bank-statement ingest, no matching | **P2** | peer scan (NetSuite, Dynamics) |
| FIN-12 | **Credit memo / debit memo** | No document types, no reversal logic | **P1** | `ERP_CRM_GAP_ANALYSIS § 18` P0 |
| FIN-13 | **Fixed-asset register** | No module | **P2** | peer scan |
| FIN-14 | **Finance webhooks** | Listed in `README.md` legacy mapping; service has none | **P2** | `README.md` legacy mapping |

### 2.5 CRM module (under ERP) — stub

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| CRM-1 | **Capture and qualify a lead** | No `leads` table, no `Lead` API | **P0** | `ERP_CRM_GAP_ANALYSIS § 3` P0 |
| CRM-2 | **Move an opportunity through pipeline stages** | No `opportunities` / `pipelines` / `pipeline_stages` tables | **P0** | same |
| CRM-3 | **Create a quote** | No `quotes` / `quote_lines` table | **P0** | same |
| CRM-4 | **Convert a quote to a sales order** | No `POST /quotes/{id}/convert-to-so` | **P0** | `ERP_CRM_GAP_ANALYSIS § 11` |
| CRM-5 | **Manage contacts (sub-entity of party)** | No `contacts` table, no nested routes | **P0** | `ERP_CRM_GAP_ANALYSIS § 10` |
| CRM-6 | **View Party 360° (customer + orders + invoices + tickets + activities)** | No `/dashboard/customers/{id}/360` aggregation, no `activities` table | **P0** | `ERP_CRM_GAP_ANALYSIS § 18` P0 |
| CRM-7 | **Log an activity (call, email, meeting)** | No `activities` table | **P1** | `ERP_CRM_GAP_ANALYSIS § 8` |
| CRM-8 | **Manage a contract** | No `contracts` / `contract_clauses` table | **P1** | same |
| CRM-9 | **Open and route a support ticket** | No `tickets` / `ticket_comments` table | **P1** | same |
| CRM-10 | **Run the CRM Sales Coach agent** | Not implemented; relies on RAG over Party + orders, which also doesn't exist | **P1** | `ERP_CRM_GAP_ANALYSIS § 7` |
| CRM-11 | **Score account health / churn signal** | No signal computation, no model | **P2** | same |
| CRM-12 | **NL-query the customer timeline** | No RAG over Party events | **P2** | same |

### 2.6 Trade compliance (HTS / customs / FTZ / screening) — well-developed but integration gaps

The trade module is the *most complete* part of the ERP service (1280 lines across 7 files). Real gaps are mostly about wiring and edge cases.

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| TR-1 | **Resolve an HTS code to a tariff at quote time** | `POST /trade/hts/lookup` exists but is not called from the SO create path | **P0** | `src/api/orders.py` (no HTS hook) |
| TR-2 | **Stamp an SO line with computed duty** | No `duty_amount` column on `sales_order_lines` | **P1** | `SalesOrderLine` (no duty field) |
| TR-3 | **Tie a customs entry to a PO receipt** | `CustomsEntry` has a `customs_entry_id` link field but no automatic linkage job | **P1** | `src/api/trade.py:110-115` |
| TR-4 | **Auto-screen every Party on create** | Screening happens inside `create_party` but only for `customer|vendor|carrier`; `employee`/`internal_org` skip screening | **P1** | `src/api/parties.py:29` regex |
| TR-5 | **Re-screen on Party update** | `PATCH /parties/{id}` accepts changes but does not re-screen unless `name` changes (only `name` is the screening key in current implementation) | **P1** | `src/trade/screening.py` (verify) |
| TR-6 | **Roll up FTZ inventory into GL deferred-duty account** | FTZ admission emits an event, but no GL post | **P1** | `src/trade/ftz.py` + FIN-3 |
| TR-7 | **Track FTZ removal / export** | Only admit is implemented; no `REMOVE_FROM_FTZ` | **P1** | `src/trade/ftz.py` |
| TR-8 | **Hold a customs entry pending FTZ admit** | No state machine; only `draft` and `filed` | **P2** | `src/trade/customs.py` |
| TR-9 | **Publish customs `CUSTOMS_FILED` for downstream Compliance** | Event payload built but only on file, no cross-service consumer tested | **P2** | `src/trade/customs.py:file_customs_entry` |

### 2.7 Platform / integrations — partial

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| PL-1 | **Onboard a new tenant (full path)** | `create_tenant` is a stub — no Keycloak realm creation, no DB provisioning, no `tenant-created` outbox event, no idempotent WMS/TMS/QMS/MRP provisioning consumer | **P0** | `src/integrations/tenant_onboarding.py:28-44` |
| PL-2 | **Connect a real platform (e.g. Shopify, NetSuite)** | `connect_platform` records a row but no OAuth handshake, no token storage, no sync job | **P0** | `src/integrations/connector_flow.py:77-148` |
| PL-3 | **Disconnect a platform and revoke tokens** | `disconnect_platform` flips status to `disconnected` but does not revoke OAuth tokens or call the platform's revoke endpoint | **P1** | `src/integrations/connector_flow.py:150-177` |
| PL-4 | **Run a connector sync** | No `last_sync_at` job, no scheduler | **P1** | `PlatformConnector.last_sync_at` (column exists, no writer) |
| PL-5 | **Webhooks out (publisher)** | No outbound webhook delivery, no signing, no retry | **P1** | `README.md` legacy "finance webhooks" |
| PL-6 | **Webhooks in (receiver)** | No inbound webhook controller | **P1** | same |
| PL-7 | **Process inbound emails** | No email ingest pipeline | **P2** | `ERP_CRM_GAP_ANALYSIS § 13` |
| PL-8 | **Send SMS** | No SMS adapter | **P2** | same |
| PL-9 | **Process payments (Stripe / ACH)** | No payment-gateway connector | **P0** | peer scan |

### 2.8 RBAC / SoD — thin

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| RBAC-1 | **Define a custom role** | No `roles` / `role_permissions` / `user_roles` tables in service; no `PermissionService` | **P0** | `ERP_CRM_GAP_ANALYSIS § 5` |
| RBAC-2 | **Enforce module-level ACL on a mutation** | No ACL check in any router; all routes trust the JWT/tenant header only | **P0** | `src/api/orders.py` (no permission gate) |
| RBAC-3 | **SoD (segregation of duties) — e.g. approver ≠ requester on PO** | No SoD rules engine | **P1** | `ERP_CRM_GAP_ANALYSIS § 12` |
| RBAC-4 | **Manage roles/permissions from the UI** | No `/settings/roles`, no `/settings/permissions` routes in the ERP service | **P0** | `ERP_CRM_GAP_ANALYSIS § 4` |
| RBAC-5 | **Audit log read API** | `004_audit_log_immutable.py` migration exists but no read endpoint, no per-tenant query | **P1** | `migrations/versions/004_audit_log_immutable.py` |

### 2.9 Observability / ops — missing

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| OPS-1 | **Emit OpenTelemetry spans from a request to a consumer** | `shared-kernel` mentions OTel, no instrumentation in `services/erp/src/` | **P0** | grep `services/erp/src` for `tracer`/`span` — none |
| OPS-2 | **Emit metrics (request count, latency, queue lag)** | No `prometheus_client` / `OTLPMetricExporter` import in ERP service | **P0** | same |
| OPS-3 | **Structured logging with trace_id propagation** | `trace_id` exists in outbox payload but not in app logs | **P1** | `src/o2c/order_flow.py:67-95` |
| OPS-4 | **Readiness probe distinct from liveness** | Only `/health` exists, no `/ready` | **P0** | `src/app.py:63-65` |
| OPS-5 | **Per-tenant metrics in Prometheus** | No `tenant_id` label | **P1** | n/a |
| OPS-6 | **Dead-letter queue for outbox** | Outbox poller exists but no DLQ for events that fail N times | **P1** | `src/outbox_poller.py` (verify) |
| OPS-7 | **Schema-registry-driven event validation** | Payload is built ad-hoc, not Avro-validated against Apicurio | **P0** | `BUILD_STATUS.md` "Apicurio compatibility gate" P1 |

### 2.10 Multi-tenancy — proven pattern, gaps remain

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| TEN-1 | **Cross-tenant admin (support mode)** | No `tenant_id = *` bypass for ops; RLS only allows per-tenant queries | **P1** | `src/db.py: set_tenant_context` (RLS only) |
| TEN-2 | **Tenant suspend / restore** | No `tenants.status`, no admin endpoint | **P1** | `src/integrations/tenant_onboarding.py` (stub) |
| TEN-3 | **Per-tenant feature flags** | `shared-flags` is a stub; no per-tenant override | **P1** | `BUILD_STATUS.md` "shared-flags STUB" |
| TEN-4 | **Per-tenant custom fields** | No extension-table pattern | **P2** | `ERP_CRM_GAP_ANALYSIS § 4` |
| TEN-5 | **Sandbox tenant clone** | No clone/restore flow | **P2** | `ERP_CRM_GAP_ANALYSIS § 3` |

### 2.11 AI / RAG / agents — under ERP

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| AI-1 | **Run any agent from the ERP service** | `ai-agents/platform/` is on a branch, not `main`; no in-service registry client | **P0** | `BUILD_STATUS.md` "Agent Platform 5%" |
| AI-2 | **RAG-query over Party / orders / invoices** | No `document_chunks` / `embeddings` tables, no vector store in service | **P1** | `ERP_CRM_GAP_ANALYSIS § 7` |
| AI-3 | **Demand-sensing agent (forecast for MRP)** | Lives in legacy monolith; no ERP port | **P1** | same |
| AI-4 | **CRM Sales Coach (suggest-only)** | Not implemented | **P1** | same |
| AI-5 | **Finance Insight agent** | Not implemented | **P1** | same |
| AI-6 | **Inventory Optimization / Procurement agent** | Templates are stubs | **P2** | same |
| AI-7 | **Capture AI feedback (`ai_feedback`)** | No `ai_feedback` table in service | **P2** | `ERP_CRM_GAP_ANALYSIS § 8` |

### 2.12 HR / Legal modules — entirely stub

| # | Action you can't perform | Why | Severity | Source |
|---|---|---|---|---|
| HR-1 | **CRUD employees** | No `employees` / `departments` / `org_units` table in service | **P1** | `README.md` "HR: Stub" + `ADR-0016` |
| HR-2 | **Manager hierarchy / org chart** | No recursive query, no API | **P2** | same |
| HR-3 | **Payroll hand-off** | No payroll export, no GL post | **P2** | same |
| LGL-1 | **CRUD contracts / clauses** | No `contracts` / `contract_clauses` / `approval_workflows` table in service | **P1** | `README.md` "Legal: Stub" + `ADR-0016` |
| LGL-2 | **Approval workflow engine** | No approval framework | **P1** | same |
| LGL-3 | **E-signature integration** | No DocuSign / Adobe Sign hook | **P2** | peer scan |

---

## 3. Architectural / structural gaps (not "actions" but explain *why* the actions can't be done)

These are the root causes that produce the 90+ action gaps above.

| # | Structural gap | Cascading effect |
|---|---|---|
| S-1 | **No Temporal workflow runtime in service** | O2C-10, P2P-9 — every multi-step saga is hand-rolled, no retries/compensations |
| S-2 | **No real Kafka consumer wired to service** | Every "events consumed" line in SPEC is a stub in `src/consumers/` |
| S-3 | **No repository layer; routers reach into SQLAlchemy directly** | O2C, FIN, GL, CRM all suffer — no testable service layer |
| S-4 | **No event schema validation (no Apicurio client)** | Outbox payload dicts can drift; consumers can break silently |
| S-5 | **No `PermissionService`** | RBAC-1..4 — every mutation is untrusted |
| S-6 | **No `NotificationService`** | CRM-12, FIN-12, OPS — no in-app / email / SMS |
| S-7 | **No `ImportExportService`** | MD-9, FIN-9 — no bulk loads |
| S-8 | **No `SearchService`** | MD-10, CRM-6 — no 360 view, no global search |
| S-9 | **No `AgentOrchestrator`** | AI-1..7 — agents are external stubs |
| S-10 | **Legacy monolith still owns most of the truth** | The frontend is wired to `:8010`, not `:8001`; ERP service is a parallel write target, not the SoT |
| S-11 | **No `services/erp/src/crm/` (or `modules/crm/` is just README)** | CRM-1..12 — no module code at all |
| S-12 | **No Pydantic schemas for entities** | O2C-4, FIN-2, CRM-1 — every API surface is hand-coded |
| S-13 | **Hardcoded `body.lines[0]`** | O2C-1 — single line is the only working order |
| S-14 | **No `pricing` / `tax` / `fx_rates` lookup anywhere** | O2C-11..13 — orders are priced by client input |
| S-15 | **No idempotency on PATCH/DELETE** | O2C-4, MD-7 — retries are unsafe |
| S-16 | **No OpenAPI export of the service's contract** | No `packages/shared-clients` TS SDK can be generated; frontend can't have a typed client |
| S-17 | **No contract tests against WMS / TMS / QMS / MRP** | Cannot prove any cross-service loop |
| S-18 | **No `app.state.kafka_producer`; outbox poller alone** | The deploy path is OK for a single replica; horizontal scale needs partitioned poller — not built |
| S-19 | **No DB-level soft-delete pattern** | MD-8 — every delete is destructive; no recycle bin UX possible |
| S-20 | **No `events_raw` table for at-least-once consumption observability** | Consumers can silently lose messages |

---

## 4. Risk-ranked punch list (what to fix in v2 first)

Cut for "drop-in merge to `feat/erp-crm` later":

| Order | Theme | Items covered | Notes |
|---|---|---|---|
| **W0 — Foundations** | Schemas, RBAC, idempotency, OTel | S-1..5, S-12, S-15, S-16, OPS-1, OPS-2, OPS-4, OPS-7, RBAC-1, RBAC-2 | All other work depends on this |
| **W1 — O2C complete** | Multiline, credit, reservation, cancel, hold, invoice, payment, FIFO, tax, FX | O2C-1..15, TR-1, TR-2, FIN-3, FIN-8, FIN-9, FIN-12 | Single vertical that proves the loop |
| **W2 — P2P complete** | Requisition, PO, approval, receipt, 3-way, AP, supplier suspend | P2P-1..9 | Second saga; needs Temporal |
| **W3 — Master data** | Products, BOMs, UoM, locations, price lists, party full, search, bulk import | MD-1..11 | Unblocks every other module |
| **W4 — Finance / GL** | COA, journal entry, auto-post, period close, AR/AP aging, revaluation | FIN-1..14 | Closes O2C/P2P |
| **W5 — CRM core** | Contacts, leads, opps, pipeline kanban, quotes → SO, activities, 360 view | CRM-1..9 | Wave 1 of CRM module |
| **W6 — Platform / integrations** | Tenant onboarding, OAuth connector, webhooks, payments, RBAC UI | PL-1..9, RBAC-3..5, FIN-13 | Plumbing |
| **W7 — CRM AI + notifications** | Sales Coach (suggest), notifications center, saved views, custom fields, tickets | CRM-10..12, AI-2, AI-4, AI-5, OPS-3, OPS-5..6, TEN-3..5 | Stickiness |
| **W8 — HR / Legal** | Employee CRUD, contract CRUD, approval workflow engine | HR-1..3, LGL-1..3, FIN-11 | Round out ERP modules |
| **W9 — Trade polish** | HTS auto-resolve, FTZ removal, re-screen on update | TR-3..9 | Operational hardening |
| **W10 — Operational readiness** | Schema-registry, contract tests, per-tenant metrics, DLQ | S-4, S-7..8, S-17, S-18, S-20, OPS-7 | Production gates |

Total: **90+ action gaps + 20 structural gaps = ~110 items** for the v2 service to actually cover SPEC.

---

## 5. Bottom line

The current `services/erp` is a **thin shell over the O2C happy path + a well-built trade-compliance module**. It cannot:

- close a sale (no invoice, no payment, no GL post, no tax, no FX)
- buy anything (no PO, no 3-way match, no AP)
- hold a customer relationship past a one-line order (no contacts, no pipeline, no quote, no 360 view)
- enforce who can do what (no RBAC)
- observe itself (no OTel, no metrics, no DLQ)
- run its own sagas (no Temporal)
- consume any of the events the SPEC says it must consume
- host the CRM, HR, Legal, Finance, Procurement modules that the README and ADR-0016 say belong here

That's the audit. v2 needs to close ~110 items to be SPEC-complete. The 10-wave plan above is a merge-friendly sequence — each wave ships behind a feature flag, lands on a short-lived `feat/erp-v2-*` branch off `main`, and rolls into `feat/erp-crm` on the parent repo once green.
