# ADR-0001 — Wave-based delivery, no big-bang cutover

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | W0 (sets the pattern); applies to W1..W10 |
| Supersedes | — |
| Related | [`MERGE_PLAN.md`](../../MERGE_PLAN.md), [`docs/SPEC.md`](../SPEC.md), [ADR-0001 in parent repo] |

## Context

`cautious-goggles/services/erp/` is at ~15% of SPEC scope. A clean
rebuild is the only way to land the full module set (O2C, P2P, GL, CRM,
HR, Legal, Trade, Platform) on a multi-month timeline without
colliding with the in-flight `feat/erp-crm` work in the parent repo.

Three options were considered:

1. **Rebuild in place on a long-lived `feat/erp-v2` branch in the
   parent.** Block the merge train for weeks, fight constant rebase
   pain against other in-flight work, force the frontend to keep
   working against a half-rewritten service.
2. **Big-bang cutover in a single PR.** One massive PR after 6+ months
   of work, unmergeable in chunks, no signal to the rest of the team
   about progress, single point of failure for review.
3. **Isolated repo + wave-by-wave PRs.** Work happens in
   `cautious-goggles-erp-v2` on trunk, each wave ships as one PR into
   the parent's `feat/erp-crm`. Frontend keeps talking to v1
   (`:8010`) until W6; from W6 onward Kong gradually shifts traffic.

## Decision

We adopt option 3.

- **Work repo:** `cautious-goggles-erp-v2` (this repo). Trunk-based
  short-lived `feat/erp-v2-wN-*` branches.
- **Merge target:** `cautious-goggles/feat/erp-crm`, one PR per wave.
- **Cutover window:** legacy `services/erp/` v1 stays in service until
  v2 reaches W6 parity on the routers the frontend actually touches.
  From W6 onward, Kong shifts the affected routes to `:8001` (v2)
  behind the same paths, with a 301/308 fallback map.
- **First wave is plumbing only.** W0 ships schemas, RBAC tables,
  observability, idempotency, OpenAPI export — no business behaviour.
  This proves the cross-cutting infrastructure before any feature lands
  on top of it.

## Consequences

### Positive

- The frontend and other services keep working against v1 for the
  whole wave train. No flag day.
- Each wave's PR is reviewable in a sitting. ~14 days of work per
  PR, not 6 months.
- Audit items in [`AUDIT.md`](../../AUDIT.md) close in priority order
  (foundations first, O2C next, P2P next) so we get observability,
  RBAC, and idempotency everywhere before any new feature.
- CI in this repo runs on every push without blocking the parent's
  train. We can fast-follow on lint/format/types without disturbing
  parent CI.

### Negative / costs

- Two repos to keep in sync. `MERGE_PLAN.md` is the single source of
  truth for which commit hash in this repo's `main` corresponds to
  each wave PR in the parent. Drift is possible; the parent-PR
  template requires the wave's source commit hash in its body.
- A new contributor has to understand the wave topology before they
  can land a fix. The trade is documented in `DEVELOPMENT.md` and
  `MERGE_PLAN.md`.
- Cherry-pick merges can lose the bisect story if a wave's commits
  land in a different shape in the parent. We squash each wave on the
  way into the parent to keep history linear.

### What we don't do

- We do **not** create a long-lived `feat/erp-v2` branch in the
  parent. The parent repo's branch strategy forbids it; the merge
  train uses `feat/erp-crm` as the integration branch.
- We do **not** ship partial routers behind a feature flag in v1.
  v2 ships complete routers; v1's routes stay until the Kong shift
  retires them.
