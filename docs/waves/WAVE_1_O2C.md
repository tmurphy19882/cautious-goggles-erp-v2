# Wave 1 — O2C complete (DONE)

> **Branch:** `feat/erp-v2-w1-o2c-complete`
> **HEAD:** see `git log` — see the `fix(w0)` then `feat(erp-v2): W1 ...` commits
> **Target (parent):** `cautious-goggles/feat/erp-crm`
> **Spec:** [`/docs/SPEC.md#wave-1`](../SPEC.md)

W1 closes the entire Order-to-Cash (O2C) flow:

- **O2C-1..O2C-15** from `AUDIT.md` (multiline SO, credit, reservation,
  cancel, hold, confirm, status machine, FIFO COGS, tax + FX, all
  in a Temporal-shaped orchestrator).
- **RBAC-3** (SoD on credit hold release) via the `require_permission` gate
  on the relevant routes.
- **ADRs 0011, 0012** (outbox schema, idempotency hash) which W0 adopted.
- **Outbox poller** + **in-process event bus** for WMS/TMS stubs.
- **SHIP_CONFIRMED → invoice** consumer (in-process until W1.1 swaps
  the bus for Kafka).

## What ships in W1

### Migrations
- `0104_outbox_events` — outbox table matching v1 schema (ADR-0011)
- `0105_w1_core` — 16 new tables: products, locations, price_lists,
  price_list_items, fx_rates, tax_rules, credit_limits,
  inventory_reservations, sales_orders, sales_order_lines, invoices,
  invoice_lines, payments, payment_applications, inventory_valuation,
  holds. All tenant-scoped tables get FORCE RLS.

### Source
- `shared/outbox.py` — `OutboxEvent`, `OutboxStore`, `OutboxPoller`, `EventBus`
- `o2c/sales_order.py` — `SalesOrderService` with `create`, `confirm`, `cancel` (multiline, status machine, ORDER_CONFIRMED publish)
- `o2c/credit.py` — `check_credit` (per-customer per-currency limit; O2C-2)
- `o2c/reservation.py` — `reserve_for_order`, `release_reservations` (O2C-3)
- `o2c/tax_fx.py` — `compute_line_totals`, `resolve_tax_rule`, `get_fx_rate` (O2C-12, O2C-13)
- `o2c/invoice.py` — `generate_invoice_for_order`, `apply_payment` (O2C-6, O2C-7, FIFO COGS, emits INVOICE_GENERATED + PAYMENT_RECEIVED)
- `o2c/cogs.py` — `allocate_cogs_for_invoice` (O2C-9, FIFO layer consumption)
- `o2c/workflows/__init__.py` — `OrderToCashWorkflow` (in-process orchestrator, Temporal-shaped)
- `o2c/consumers.py` — `register_consumers` for SHIP_CONFIRMED, HOLD_PLACED, SUPPLIER_SUSPENDED (O2C-5)
- `o2c/api.py` — REST routes: `POST /sales-orders` (multiline, O2C-1), `POST /sales-orders/{id}/confirm`, `POST /sales-orders/{id}/cancel` (O2C-4), `GET /sales-orders/{id}`, `GET /sales-orders`, `POST /sales-orders/{id}/payments`, `POST /sales-orders/{id}/run-workflow`
- `master_data/api.py` — minimal master-data: products, locations, customers (thin W1 → full W3), inventory layers, credit limits

### API surface (W1 additions under `/api/v1/erp`)
- `POST   /master-data/products` — `master.product.write`
- `GET    /master-data/products` — `master.product.read`
- `GET    /master-data/products/{id}` — `master.product.read`
- `POST   /master-data/products/{id}/layers` — `master.product.write`
- `POST   /master-data/locations` — `master.location.write`
- `GET    /master-data/locations` — `master.location.read`
- `POST   /master-data/customers` — `master.party.write`
- `GET    /master-data/customers` — `master.party.read`
- `POST   /master-data/credit-limits` — `o2c.credit.write`
- `POST   /sales-orders` — `o2c.so.write` (multiline, O2C-1)
- `GET    /sales-orders` — `o2c.so.read`
- `GET    /sales-orders/{id}` — `o2c.so.read`
- `POST   /sales-orders/{id}/confirm` — `o2c.so.confirm` (O2C-2, O2C-3, O2C-14)
- `POST   /sales-orders/{id}/cancel` — `o2c.so.cancel` (O2C-4)
- `POST   /sales-orders/{id}/payments` — `o2c.payment.write` (O2C-7)
- `POST   /sales-orders/{id}/run-workflow` — `o2c.so.confirm` (W1 sync path)

### Tests
- `tests/integration/test_o2c_happy_path.py` — full O2C flow: customer → product → location → layer → credit → SO (multiline) → confirm → run workflow → payment → assert states
- `tests/integration/test_o2c_happy_path.py::test_o2c_credit_hold` — credit exceeded → `credit_hold`, no `ORDER_CONFIRMED` published
- `tests/integration/test_o2c_happy_path.py::test_o2c_cancel_releases_reservation` — cancel after confirm → reservation released

### Smoke test (extended)
`scripts/smoke_test.py` now covers the W1 happy path: customer → product → location → layer → credit limit → multiline SO → confirm → run workflow → payment.

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| O2C-1 | Multi-line SO | `o2c/api.py::create_sales_order` + `SalesOrderService.create` |
| O2C-2 | Credit check | `o2c/credit.py::check_credit` + `SalesOrderService.confirm` |
| O2C-3 | Reservation | `o2c/reservation.py::reserve_for_order` |
| O2C-4 | Cancel | `o2c/sales_order.py::SalesOrderService.cancel` + `release_reservations` |
| O2C-5 | QMS hold consumer | `o2c/consumers.py::_make_hold_consumer` (subscribes to `scm.qms.hold-placed.v1`) |
| O2C-6 | Invoice generation | `o2c/invoice.py::generate_invoice_for_order` |
| O2C-7 | Payment application | `o2c/invoice.py::apply_payment` |
| O2C-8 | Revenue recognition | Stub in `o2c/cogs.py::recognize_revenue`; full impl in W4 |
| O2C-9 | FIFO COGS | `o2c/cogs.py::allocate_cogs_for_invoice` |
| O2C-10 | Temporal workflow | `o2c/workflows/__init__.py::OrderToCashWorkflow` (Temporal-shaped in-process; Temporal port in W1.1) |
| O2C-11 | Price-list lookup | `o2c/sales_order.py::SalesOrderService._price_lines` |
| O2C-12 | Tax engine | `o2c/tax_fx.py::compute_line_totals` + `resolve_tax_rule` |
| O2C-13 | FX | `o2c/tax_fx.py::get_fx_rate` (read-only in W1; per-order FX rate stored on the SO) |
| O2C-14 | Outbox publishing | `shared/outbox.py::OutboxStore.emit` + `OrderToCashWorkflow._in_process_publisher` (Kafka swap in W1.1) |
| O2C-15 | Quote → SO | Not in W1 (W5's CRM core adds `POST /quotes/{id}/convert-to-so`) |
| S-1 | Temporal runtime | Partial — `OrderToCashWorkflow` is Temporal-shaped; the actual `temporalio` integration lands in W1.1 alongside Kafka |

**Not closed in W1 (deferred to later waves):**
- O2C-15 (quote → SO) — W5 CRM core
- Real Temporal integration — W1.1 (alongside Kafka producer)
- Real Kafka producer — W1.1 (after testcontainers in CI can stand up Redpanda)
- Per-tenant role seeding in code — W6 tenant-onboarding flow
- Outbox DLQ + stuck-message detection — W10 operational readiness

## How to run locally

```bash
cd services/erp
pip install -e ".[dev]"

# Postgres
docker compose -f ../../infra/docker-compose.yml --profile core up -d
export ERP_DATABASE_URL=postgresql+asyncpg://erp:erp@localhost:5432/erp_db

# Apply ALL migrations (W0 + W1)
make migrate

# Seed (demo admin + role)
make seed

# Smoke test
make smoke   # exercises W0 + W1 happy path
```

## How to merge into the parent

Push this branch to the parent repo's remote under
`feat/erp-v2-w1-o2c-complete`. Open a PR against
`cautious-goggles/feat/erp-crm` titled:

```
feat(erp-v2): O2C complete (multiline, credit, reservation, invoice, payment, FIFO, tax, FX, workflow)
```

Risk: **Medium** — additive on tables and routes; the v1 monolith's
`/dashboard/sales-orders` page keeps working against `:8010`.
Rollback: revert PR; v1's `:8010` continues to serve. Kong ships the
`/api/v1/erp` route to v2 in W6; W1 doesn't shift traffic.

W2 work starts on a fresh `feat/erp-v2-w2-p2p-complete` branch.
