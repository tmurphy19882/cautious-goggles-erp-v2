# Wave 5 — CRM core (DONE)

> **Branch:** `feat/erp-v2-w5-crm-core`
> **Spec:** [`/docs/SPEC.md#wave-5`](../SPEC.md)

W5 ships the CRM core: leads, opportunities, pipeline, kanban
stages, quotes, and the long-promised **quote → SO conversion**
(closes O2C-15 from the audit).

## What ships

### Migration
- `0108_w5_crm` — 9 new tables: pipelines, pipeline_stages, leads,
  opportunities, quotes, quote_lines, activities, tickets, ticket_comments.
  All FORCE RLS.

### Source
- `crm/service.py` — `LeadService` (create / qualify / convert),
  `QuoteService` (create / send / accept / **convert_to_sales_order**).
  Convert calls into `o2c.sales_order.SalesOrderService.create` with
  the quote's lines copied, then sets `converted_sales_order_id` on
  both the quote and the parent opportunity. Closes O2C-15.
- `crm/api.py` — REST routes for pipelines, stages, leads (create /
  qualify / convert), quotes (create / send / accept / convert-to-so).

### Tests
- `tests/integration/test_crm_happy_path.py::test_crm_quote_to_so_happy_path` — pipeline + stages + lead + qualify + convert (lead → party + opportunity) + credit limit + quote + send + accept + convert-to-SO + SO confirm. End-to-end CRM → O2C handoff.

### Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| O2C-15 | Quote → SO conversion | `QuoteService.convert_to_sales_order` + `POST /crm/quotes/{id}/convert-to-so` |
| CRM-1 | Lead capture | `LeadService.create` |
| CRM-2 | Lead qualification | `LeadService.qualify` |
| CRM-3 | Pipeline + kanban | `pipelines` + `pipeline_stages` |
| CRM-4 | Opportunity stages | `opportunities.stage_id` + transitions |
| CRM-5 | Quote creation + convert | `QuoteService` |
| CRM-6 | Activity timeline | `activities` table (full UI in W7) |
| CRM-7 | Tickets | `tickets` + `ticket_comments` (full UI in W7) |

## How to merge

Push `feat/erp-v2-w5-crm-core` to the parent, open a PR titled:

```
feat(erp-v2): CRM core (leads, opportunities, pipeline, quotes → SO)
```

Risk: **Low** — additive. W6 starts on `feat/erp-v2-w6-platform`.
