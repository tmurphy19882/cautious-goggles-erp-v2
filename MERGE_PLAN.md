# Merge Plan — `cautious-goggles-erp-v2` → `cautious-goggles/feat/erp-crm`

> **Goal:** ship `services/erp` v2 in waves, each wave = one PR into the parent
> repo's `feat/erp-crm` branch. No big-bang cutover. Frontend keeps talking
> to `:8010` (legacy) until v2 reaches W6 parity; from W6 onward, Kong
> gradually shifts traffic to `:8001`.

## Source of truth

| Question | Answer |
|----------|--------|
| Where does work happen? | **`cautious-goggles-erp-v2`** (this repo), `main` + short-lived `feat/erp-v2-wN-*` |
| Where does it land? | **`cautious-goggles/feat/erp-crm`** via PR per wave |
| When is the legacy `services/erp/` v1 deleted? | After W6 (when Kong shifts default traffic) **and** v2 passes parity tests on every router the frontend touches |
| When do we cut a `feat/erp-v2` long-lived branch in parent? | **Never.** All work goes into `feat/erp-crm` directly. The parent repo's branch strategy forbids `feat/erp-v2` per `BRANCH_STRATEGY.md`. |
| Who owns the merge? | Whoever opens the wave's PR; the `feat/erp-crm` branch owner is the gate. |

## Branch topology

```
this repo (cautious-goggles-erp-v2)
└── main                                       ← trunk; only ships via PR
    ├── feat/erp-v2-w0-foundations            ← W0 work
    ├── feat/erp-v2-w1-o2c-complete           ← W1 work
    ├── feat/erp-v2-w2-p2p-complete           ← W2 work
    └── ... (one branch per wave)

parent repo (cautious-goggles)
└── main
    └── feat/erp-crm                           ← receives wave-by-wave PRs
```

Wave branches in *this* repo are rebased (or merged + squashed) into *this* `main`
first; only then is the wave mirrored up to the parent as one PR. **No parent
PR contains unreviewed code.**

## Per-wave PR template

Every PR into the parent uses this skeleton (lives at
`docs/parent-pr-template.md` in W0):

```markdown
## Wave N — <name>

**Source branch:** `cautious-goggles-erp-v2/main` (squashed from `feat/erp-v2-wN-*`)
**Target branch:** `cautious-goggles/feat/erp-crm`
**Spec section:** see `cautious-goggles-erp-v2/docs/SPEC.md#wave-N`
**Audit items closed:** see `cautious-goggles-erp-v2/AUDIT.md#<punch-list-row>`

### What's in this PR
- <one-line per file or per group>
- <schema change → link migration>
- <event topic added → link Avro schema + manifest update>

### Backend checklist
- [ ] Alembic migration added; tested `upgrade` and `downgrade`
- [ ] Outbox topic registered in `packages/shared-events/CATALOG.md`
- [ ] Avro schema in `packages/shared-events/schemas/<topic>.v1.avsc` (if new topic)
- [ ] `PermissionService` gates on every mutating route
- [ ] Idempotency middleware on every POST/PATCH
- [ ] OTel spans + Prometheus metrics on every handler
- [ ] OpenAPI tag + description on every route
- [ ] Pytest covers happy path + 1 auth-failure + 1 idempotency-replay

### Frontend impact
- [ ] None (this wave is service-only)
- [ ] OR: typed SDK regenerated; pages listed; Kong shift percentage

### Risk
- [ ] Low — additive only, no schema delete, no event rename
- [ ] Medium — additive but requires new env var / migration step
- [ ] High — schema rename, event rename, or replaces v1 endpoint

### Rollback
- <one-liner on how to revert this PR>
```

## Wave-by-wave cut plan

| Wave | Source branch in this repo | PR title in parent | Risk | Rollback |
|------|----------------------------|---------------------|------|----------|
| W0 Foundations | `feat/erp-v2-w0-foundations` | `chore(erp-v2): foundations (schemas, RBAC, OTel, idempotency, OpenAPI)` | Low | Revert PR; v1 unaffected |
| W1 O2C complete | `feat/erp-v2-w1-o2c-complete` | `feat(erp-v2): O2C complete (multiline, credit, reservation, cancel, hold, invoice, payment, FIFO, tax, FX)` | Medium | Keep v1 endpoint as fallback; flag `erp.v2.o2c.enabled` |
| W2 P2P complete | `feat/erp-v2-w2-p2p-complete` | `feat(erp-v2): P2P complete (requisition, PO, approval, receipt, 3-way, AP, suspend)` | Medium | Same; flag `erp.v2.p2p.enabled` |
| W3 Master data | `feat/erp-v2-w3-master-data` | `feat(erp-v2): master data (products, BOMs, UoM, locations, price lists, party 2.0, search, import)` | Low | Additive; v1 still serves Party |
| W4 Finance/GL | `feat/erp-v2-w4-finance` | `feat(erp-v2): finance/GL (COA, journal, auto-post, period close, AR/AP aging, FX reval, tax)` | Medium | v1 finance untouched |
| W5 CRM core | `feat/erp-v2-w5-crm-core` | `feat(erp-v2): CRM core (contacts, leads, opps, pipeline, quotes, activities, 360 view)` | Low | Additive |
| W6 Platform/integrations | `feat/erp-v2-w6-platform` | `feat(erp-v2): platform (tenant onboarding, OAuth, webhooks, payments, RBAC UI)` | **High** | First wave that may displace v1 tenant-onboarding stub |
| W7 CRM productivity | `feat/erp-v2-w7-crm-productivity` | `feat(erp-v2): CRM notifications, inbox, saved views, custom fields, tickets` | Low | Additive |
| W8 HR / Legal | `feat/erp-v2-w8-hr-legal` | `feat(erp-v2): HR + Legal modules (employees, contracts, approval engine)` | Low | Additive |
| W9 Trade polish | `feat/erp-v2-w9-trade` | `feat(erp-v2): trade polish (HTS auto-resolve, FTZ removal, re-screen)` | Low | v1 trade keeps working |
| W10 Operational readiness | `feat/erp-v2-w10-ops` | `chore(erp-v2): Avro gate, contract tests, per-tenant metrics, DLQ` | Low | v1 still on its own OTel path |

## Final cutover (after W10)

1. **Backend:** v1 `services/erp/src/api/{orders,parties}.py` routers are
   deleted in a separate PR titled `chore(erp): remove v1 routers (v2 in
   service)`. Anything still pointing at the old paths gets a 301/308
   redirect map.
2. **Frontend:** Next.js `serviceRouting.ts` flips `erp` to `:8001` 100%.
3. **Kong:** `infra/kong/erp-route.yml` removes the legacy shadow route.
4. **Legacy monolith:** `backend/app/domains/sales/order_to_cash.py`
   (SO workflows) and `sales/parties.py` are deleted in a final
   `chore: retire migrated monolith domains` PR. Frontend no longer talks
   to `:8010` for these.
5. **DB:** `erp_db` schema is the only one. Legacy `backend/app/domains/`
   tables are kept read-only for 90 days, then dropped in a migration.

## Conflicts to expect (and how to handle them)

| Conflict | Where | Resolution |
|----------|-------|------------|
| Both v1 and v2 define `SalesOrder` | `services/erp/src/models.py` | W1 PR replaces the v1 model; v1 routers are kept behind the flag until v2 is green |
| Both publish `scm.erp.order-confirmed.v1` | outbox | Identical payload schema; v1 is shut off behind the same flag; no consumer change |
| Migration history diverges | `migrations/versions/` | This repo's migrations re-base to the v1 latest at W0 PR time. v1's later migrations are not in v2 — v2 owns its own alembic root. |
| `backend/app/domains/sales/` (legacy) still has the truth | frontend | v2 must write through to both `erp_db` and the legacy tables via the outbox-driven `bridge` until W6 — documented in W6 PR |
| Test fixtures for legacy SO | `tests/` | Old tests stay; v2 adds a parallel `tests_v2/` until the final cutover PR |

## Day-1 checklist (do this before opening the W0 PR)

- [ ] `git remote add upstream git@github.com:tmurphy19882/cautious-goggles.git` (or whichever URL the parent uses)
- [ ] `git fetch upstream` and tag the parent `feat/erp-crm` HEAD as `parent-baseline`
- [ ] `git switch -c feat/erp-v2-w0-foundations`
- [ ] Open the W0 work (see `docs/SPEC.md#wave-0`)
- [ ] Open the W0 PR *into this repo's `main`* first
- [ ] Once green here, `git push upstream feat/erp-v2-w0-foundations:feat/erp-v2-w0-foundations` and open the parent PR using the template

## Reverting a wave

If a wave ships and breaks something in parent:

1. Revert the parent PR (single green-button revert).
2. The corresponding v2 work stays in this repo (no need to delete the branch).
3. Open an issue referencing the revert; decide whether to re-cut the wave or
   drop the items it contained.
