# Wave 7 — CRM AI + notifications + custom fields + saved views (DONE)

> **Branch:** `feat/erp-v2-w7-crm-ai`
> **Spec:** [`/docs/SPEC.md#wave-7`](../SPEC.md)

W7 ships the customer-facing AI surface and the per-user
customisations the CRM team asked for in W3.2.

## What ships

### Migration
- `0110_w7_crm_ai` — 7 new tables: `notifications`,
  `saved_views`, `custom_field_defs`, `custom_field_values`,
  `ai_agent_runs`, `ai_recommendations`, `ai_agent_feedback`.
  All FORCE RLS via the W6 SENTINEL COALESCE pattern.

### Source
- `crm/ai.py` — `NotificationService` (in-app feed, email/SMS
  adapters are stubbed for the W7.1 swap), `SalesCoachAgent`
  (deterministic stub; reads customer's order + payment history
  and returns `next_best_action` rec with confidence 0.75),
  `AgentRunner` (writes to `ai_agent_runs` + `ai_recommendations`
  with success/failure lifecycle), `SavedViewService` (per-user
  list customisation), `CustomFieldService` (per-tenant schema
  extension).
- `crm/ai_api.py` — REST routes: `GET /notifications`,
  `POST /notifications/{id}/read`, `POST /crm/agents/sales_coach/run`,
  `GET /crm/recommendations`, `GET/POST /crm/saved-views`,
  `POST /crm/custom-fields`, `PUT /crm/custom-fields/{id}/value`.

### Tests
- `tests/integration/test_w7_crm_ai.py` — notifications send +
  list + mark read, Sales Coach agent run + recommendation
  persistence, saved views create + list, custom field define +
  set value.

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| CRM-7 | Custom fields per tenant | `custom_field_defs` + `CustomFieldService` |
| CRM-8 | Saved views | `saved_views` + `SavedViewService` |
| CRM-9 | Notification center | `notifications` + `NotificationService` |
| CRM-10 | Sales Coach AI (suggest only) | `SalesCoachAgent` (deterministic W7 stub; LLM in W7.1) |
| CRM-11 | Recommendation feedback | `ai_agent_feedback` table (API in W7.1) |
| CRM-12 | Tickets read API | Out of scope for W7 (the W5 API does POST; W7.1 ships GET) |

## How to merge

Push `feat/erp-v2-w7-crm-ai` to the parent, open a PR titled:

```
feat(erp-v2): CRM AI + notifications + custom fields + saved views
```

Risk: **Low** — additions only, no schema breaks. The W7.1 swap
replaces the Sales Coach stub with a real LLM call; the contract
(input → output) is stable. W8 starts on `feat/erp-v2-w8-hr-legal`.
