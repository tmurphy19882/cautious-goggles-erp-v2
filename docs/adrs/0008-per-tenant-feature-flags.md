# ADR-0008 — Per-tenant feature flags via Unleash

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | **W0 defers** the Unleash client; W1 wires the first flags; full per-tenant UI in W7 |
| Supersedes | v1's per-deploy feature flags (rebuild of the service to enable/disable) |
| Related | [ADR-0001 — wave-based delivery](./0001-wave-based-delivery.md), [`SPEC.md`](../SPEC.md) |

## Context

v1's feature flags are tied to *deploys*: to roll out a new SO
schema, you redeploy the service with `erp.v2.so.enabled = true`.
The flag is global (all tenants) and binary (on or off). The
operational cost is high — every flag flip is a deploy, every
deploy is a maintenance window, every maintenance window is a
delay in incident response.

What v2 needs:

- **Per-tenant overrides.** Tenant A gets the new SO schema on
  Monday; tenant B stays on v1 until Friday. No redeploy.
- **Default-off for new features.** A new flag lands `off` for
  every tenant; the operator (or the tenant's admin) opts in.
- **Percentage rollout.** "Enable for 10% of tenants, randomly
  chosen, then ramp."
- **Audit.** "Who flipped this flag, when, and from what" is
  queryable, not folklore.

Options:

- **Custom flag service.** Build our own. Reinvents Unleash; the
  flag rules language becomes a small DSL; the UI is a separate
  project.
- **LaunchDarkly.** SaaS, paid, ties the platform to a third
  party. Good DX; not free.
- **Unleash.** Open source, has a self-hosted server, a Python
  client, and a per-tenant / per-user targeting model. Default
  is `off`; ops UI is the Unleash admin (or proxied into our
  RBAC UI in W6/W7).
- **Env vars + per-tenant config table.** A row in
  `tenant_config(feature, value)` queried on every flag check.
  Cheaper to start; no UI; no percentage rollouts; no
  per-segment targeting; no audit.

## Decision

**Unleash** is the flag provider. The platform wraps it in
`shared/feature_flags.py`:

```python
from shared.feature_flags import is_enabled

if await is_enabled("erp.v2.o2c.multiline_so", tenant_id=tenant_id):
    ...
```

- **Default `off` for every new flag.** The flag is registered
  in Unleash with no strategies; `is_enabled` returns `False`
  for every tenant until a strategy is added.
- **Per-tenant override** is the standard Unleash "gradual
  rollout" strategy pinned to `tenantId`. The W6 platform
  admin UI surfaces the toggle directly.
- **Percentage rollout** is a first-class Unleash strategy;
  used for staged tenant-by-tenant rollouts in W1 (O2C
  multiline) and W4 (GL auto-post).
- **Audit** is Unleash's audit log + our own
  `feature_flag_changes` row mirror (W7) so the change is
  visible from our RBAC UI without leaving the platform.
- **W0 deferral.** `shared/feature_flags.py` is a stub that
  returns `True` for a hard-coded allowlist. W1 wires the
  Unleash client. The wrap is the same; the implementation
  swaps.

## Consequences

### Positive

- **One-flag-per-feature is the rule.** New behaviour ships
  behind a flag; the flag is `off` by default; the rollout is
  per-tenant. This is the mechanism that makes wave-by-wave
  delivery safe (W6 onward, the Kong traffic shift is itself
  a flag).
- **Operators can disable a feature for one tenant without
  redeploying.** "Tenant X is hitting the new SO path; roll
  them back" is a flag flip, not a hotfix.
- **Percentage rollout** is the right tool for canarying a
  new feature against a slice of traffic.
- **Audit is real.** Every flag change is in the Unleash log
  and (W7) in our mirror.

### Negative / costs

- **Unleash is an operational dependency.** Local dev needs a
  Unleash container (or the W0 stub). Production needs an
  Unleash server. One more thing to monitor.
- **Stale flags accumulate.** A flag that is `on` for 100% of
  tenants for 6 months is dead code. W7's flag-hygiene job
  reports flags that have been at 100% for >30 days so they
  can be removed and the code path deleted.
- **The wrap is a thin shim.** A team that wants a more
  expressive rule ("enable if tenant plan is `enterprise`
  AND region is `us-east`") has to add a Unleash strategy;
  we don't build a second DSL on top.

### What we lose

- v1's "rebuild the service to enable" cost. The trade is the
  operational cost of running Unleash, which is small and
  well-understood.

### W0 scope

W0 ships:

- `shared/feature_flags.py` stub with an allowlist
  (`{"erp.v2.foundations.live"}: True`).
- A doc-only entry in [`SPEC.md`](../SPEC.md).

W0 does **not** ship:

- Unleash client.
- Unleash server in `infra/docker-compose.yml` (W1).
- Per-tenant admin UI (W6).
- Flag-change audit mirror (W7).
