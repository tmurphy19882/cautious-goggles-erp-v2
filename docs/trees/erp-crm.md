# Tree — `services/erp/src/crm/` (ERP-CRM v2)

> **Branch of record:** `feat/erp-v2-w0-foundations`
> **Wave status:** W5 planned, W7 planned (CRM AI / notifications)
> **Spec section:** [`/docs/SPEC.md#wave-5`](../SPEC.md#8-wave-5-crm-core) and [`#wave-7`](../SPEC.md#10-wave-7-crm-ai--notifications)
> **Mirror of:** v1 `trees/erp-crm.md` (parent repo), ported to v2 layout

CRM lives **inside** ERP, not as a separate service. The decision is
inherited from the parent repo's `ADR-0016` (Party-centric CRM) and is
the reason every `crm/*` sub-package is a sibling of `o2c/`, `p2c/`,
`master-data/` under `services/erp/src/`.

This tree mirrors [`trees/erp.md`](./erp.md) but focuses on the CRM
subtree: what is there in W0, what lands in W5, and what lands in W7.

## Why one service, not two

- **Party 360°.** A contact, lead, opportunity, and ticket all hang off
  a single `parties` row. A split between ERP and CRM services would
  require either (a) cross-service joins on the hot path, or (b) a
  materialised Party 360 cache that has to be kept in sync — both
  expensive and racy.
- **Sales flow integration.** Quote → SO is the same business process
  end-to-end. Splitting them across services adds a saga just to keep
  two databases in lockstep.
- **One set of permissions.** RBAC for "edit a contact" should be the
  same RBAC for "edit a customer" because contacts *are* customers at
  the data layer.

Trade-off: the `crm` module's deploy unit is the same as ERP's. W5
ships a tag in OpenAPI and a module-level view in the RBAC UI, but
they share the v2 binary until W10.

## `services/erp/src/crm/` layout

```
crm/
├── __init__.py                        # re-exports the public surface
├── contact/                           # sub-entity of Party (W5)
│   ├── api.py                         # GET/POST/PATCH/DELETE /api/v1/erp/crm/contacts
│   ├── models.py                      # SQLAlchemy 2.0 async
│   ├── schemas.py                     # Pydantic v2
│   ├── service.py                     # business rules (e.g. dedup by email+tenant)
│   └── tests/
├── lead/                              # W5
│   ├── api.py                         # includes POST /leads/{id}/convert
│   ├── models.py
│   ├── schemas.py
│   ├── service.py                     # qualification, conversion to opportunity
│   └── tests/
├── pipeline/                          # W5
│   ├── api.py                         # GET /pipelines, /pipelines/{id}/stages
│   ├── models.py                      # pipeline, stage
│   ├── service.py
│   └── tests/
├── opportunity/                       # W5
│   ├── api.py                         # POST /opps/{id}/stage-transition
│   ├── models.py
│   ├── schemas.py
│   ├── service.py                     # stage lifecycle, forecast
│   └── tests/
├── quote/                             # W5
│   ├── api.py                         # POST /quotes/{id}/convert-to-so
│   ├── models.py
│   ├── schemas.py                     # multiline-first
│   ├── service.py                     # convert_to_so() — emits SO_CREATED outbox
│   └── tests/
├── contract/                          # W5 (CRM contract), W8 (Legal owns long-term)
│   ├── api.py
│   ├── models.py
│   ├── schemas.py
│   ├── service.py
│   └── tests/
├── activity/                          # W5
│   ├── api.py                         # GET /parties/{id}/activity
│   ├── models.py                      # activity log
│   ├── service.py                     # aggregator for Party 360°
│   └── tests/
├── ticket/                            # W5
│   ├── api.py
│   ├── models.py
│   ├── schemas.py
│   ├── service.py                     # SLA timer, assignment
│   └── tests/
├── ai-coach/                          # W7
│   ├── api.py                         # POST /ai/coach/suggest
│   ├── service.py                     # RAG client, prompt, suggest-only
│   ├── rag/
│   │   ├── embeddings.py
│   │   ├── vector_store.py
│   │   └── chunker.py
│   └── tests/
├── notification/                      # W7
│   ├── api.py
│   ├── service.py                     # in-app, email, SMS adapters
│   ├── adapters/
│   │   ├── email.py
│   │   ├── sms.py
│   │   └── in_app.py
│   └── tests/
├── saved-view/                        # W7
│   ├── api.py
│   ├── models.py                      # per-role saved views
│   ├── service.py
│   └── tests/
├── custom-field/                      # W7
│   ├── api.py
│   ├── models.py                      # per-tenant schema extension
│   ├── service.py
│   └── tests/
└── api/                               # module-level aggregator router
    └── party_360.py                   # GET /api/v1/erp/parties/{id}/360
```

## Router mount

The module exposes one aggregator at the v2 API root:

```
GET /api/v1/erp/parties/{party_id}/360
```

…which joins contact, lead, opportunity, ticket, activity timelines
into a single response. Per-resource routers (`/crm/contacts`,
`/crm/leads`, …) are mounted by the platform router when the wave
ships.

## Cross-module dependencies (CRM → ERP)

| From | To | Why |
|------|----|-----|
| `crm/quote/service.py` | `o2c/sales_order/service.py` | `convert_to_so()` creates an SO with the same `party_id` |
| `crm/contact/service.py` | `master-data/party/service.py` | Contacts are Party rows with a discriminator |
| `crm/activity/service.py` | `master-data/party/service.py` | Activity log attaches to a Party |
| `crm/ai-coach/service.py` | `ai/rag/` | RAG index over Party + orders + tickets |
| `crm/notification/service.py` | `shared/feature_flags.py` | Per-tenant notification toggles |

These are the *only* cross-module edges for CRM; everything else stays
inside the `crm/` subtree.

## Permission keys (CRM surface)

Seeded in migration `0100_identity` (W0):

```
crm.contact.read, crm.contact.write
crm.lead.read,    crm.lead.write
crm.opp.read,     crm.opp.write
crm.quote.read,   crm.quote.write
crm.ticket.read,  crm.ticket.write
ai.agent.run,     ai.rag.query
```

Per-tenant roles that include these keys are seeded in
`identity.provision.provision_erp_tenant()` (W6) at tenant-onboarding
time.

## What changed vs v1

| Area | v1 (`cautious-goggles/services/crm/`) | v2 (this repo: `services/erp/src/crm/`) |
|------|---------------------------------------|------------------------------------------|
| Service boundary | Separate `services/crm/` FastAPI app | Sub-tree of `services/erp/` |
| Identity model | Separate `User` table in `crm` | Reuses ERP's `identity.users` |
| Quote → SO | Cross-service HTTP call (or manual re-entry) | In-process: `quote.service.convert_to_so()` |
| Activity timeline | Not present | `crm/activity/` joined to Party 360° |
| Tickets | Stub in v1 | Full W5 module with SLA + comments |
| AI coach | Missing | `crm/ai-coach/` (W7), RAG over Party + orders + tickets |
| Notifications | Email-only via SES | `crm/notification/` with in-app + email + SMS adapters (W7) |
| Saved views | Missing | `crm/saved-view/` (W7) |
| Custom fields | Missing | `crm/custom-field/` (W7) |
| RBAC | None | `identity.PermissionService` gates every mutator |
| Idempotency | None on POST/PATCH | Enforced by `IdempotencyMiddleware` |
| Observability | None | OTel span + Prometheus metrics on every handler |

## Audit items closed

| ID | Description | Closed by |
|----|-------------|-----------|
| CRM-1..9 | Full CRM funnel missing | W5 (planned) |
| CRM-10..12 | Saved views, notifications, custom fields | W7 (planned) |
| AI-2, AI-4, AI-5 | Sales coach / RAG | W7 (planned) |
| S-6, S-9, OPS-3, OPS-5 | Cross-cutting gaps | W7 (planned) |
