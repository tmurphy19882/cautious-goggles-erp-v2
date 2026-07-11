# ADR-0010 — Domain / infrastructure / workflows split

| Field | Value |
|-------|-------|
| Status | **Accepted** |
| Date | 2026-07-11 |
| Wave | W0 (decision); W1 first module that uses the full split (`o2c/sales-order/`) |
| Supersedes | v1's flat `src/api/`, `src/o2c/`, `src/models.py` layout |
| Related | [`docs/trees/erp.md`](../trees/erp.md), [`SPEC.md`](../SPEC.md#1-module-map) |

## Context

v1 puts every Python file at the top of `services/erp/src/`:

```
src/
├── api/
│   ├── orders.py
│   ├── parties.py
│   ├── trade.py
│   └── connectors.py
├── o2c/
│   └── order_flow.py
├── models.py
├── schemas.py
└── app.py
```

The result is that the routers, the business logic, the DB models,
and the wire schemas all share the same flat namespace. A
contributor has to read `app.py`, `api/orders.py`, and
`o2c/order_flow.py` to find the create-SO logic. The Pydantic
schemas for the same feature live in two places
(`schemas.py` and the handler's local types). Tests for the
business rule live in `tests/test_orders.py`, tests for the DB
layer live in `tests/test_models.py`, and the two never
acknowledge each other.

What v2 needs:

- **One Python package per module** in
  [`SPEC.md`](../SPEC.md#1-module-map). The package name is the
  module name; sub-packages are sub-modules.
- **A consistent internal layout** per package so a contributor
  who learns one module can navigate every other module by
  pattern-matching.
- **The router is in the module**, not in a top-level `api/`.
  The top-level `api/` only contains the FastAPI factory and
  the health endpoints.

## Decision

Every module is a Python package under
`services/erp/src/`. The internal layout is:

```
<module>/
├── __init__.py
├── api.py              # FastAPI router(s); mounted by api/main.py via app.include_router
├── service.py          # business rules; the "domain" entry points
├── models.py           # SQLAlchemy 2.0 async ORM models
├── schemas.py          # Pydantic v2 wire models
├── domain/             # entities, value objects, invariants
│   ├── entities.py
│   └── value_objects.py
├── infrastructure/     # DB repos, external clients, projections
│   ├── repo.py
│   └── projections.py
├── workflows/          # Temporal workflows + activities (sagas)
│   ├── workflow.py
│   └── activities.py
├── consumers/          # Kafka consumers (real, not stubs)
│   └── <topic>.py
├── outbox/             # outbox writers + reader for this module
│   └── writer.py
├── tests/              # module-local tests (optional; /tests/integration still ok)
└── README.md           # module-level pointer (optional, encouraged)
```

The top-level `src/` keeps only:

- `app.py` — uvicorn entry, re-exports `create_app_v2()`.
- `api/main.py` — FastAPI factory. **Does not import module
  routers.** Each wave's PR adds the `app.include_router(...)`
  call inside the module so an unfinished wave never blocks
  boot.
- `api/health.py` — `/health`, `/ready`, `/metrics`.
- `shared/`, `observability/` — cross-cutting packages (per
  [ADR-0007](./0007-soft-delete.md) and
  [ADR-0009](./0009-otel-prometheus-first-class.md)).

## Consequences

### Positive

- **Per-module locality.** Everything for SO is under
  `o2c/sales-order/`. A new contributor opens one directory.
- **One internal layout.** Domain in `domain/`, DB in
  `infrastructure/`, saga in `workflows/`, events in
  `consumers/`, outbox in `outbox/`. The pattern is identical
  across modules.
- **The router is co-located with the service.** No more
  cross-directory jumping to find what `POST /orders` does.
- **Tests are module-local.** A module's test directory
  contains its own `test_*.py` files; the top-level
  `tests/integration` is reserved for cross-module flows.

### Negative / costs

- **More directories per module.** A small module (e.g.
  `crm/contact/`) has `domain/`, `infrastructure/`, etc.,
  that may be a single file. The directory exists for the
  day the module grows.
- **Cross-module imports cross directories.** An SO that
  creates a reservation imports
  `from o2c.reservation.service import reserve`. The import
  graph is acyclic; review enforces this. A linter (W7)
  flags cross-module imports that go through `domain/`
  directly (bypassing `service.py`).
- **Boot-time wiring is per-wave.** The first wave to ship a
  module adds the `app.include_router` call; if forgotten,
  the router isn't mounted. The static guard
  `erp-v2-check-coverage` (W0) extends to "mounted
  routers must have a `require_permission` dep on every
  mutator" in W1.

### W0 scope

W0 ships:

- The empty `o2c/`, `p2p/`, `master-data/`, `finance/`,
  `crm/`, `hr/`, `legal/`, `trade/`, `platform/`, `ai/`
  packages under `services/erp/src/`. Each contains an
  `__init__.py` and a placeholder `README.md` or
  `service.py` stub.
- The full split inside `identity/`: `api.py`, `service.py`,
  `models.py`, `schemas.py`, `deps.py`. (Domain /
  infrastructure / workflows land in W1 when there's
  business logic to put in them.)

W0 does **not** ship:

- Cross-module imports — none of the empty modules import
  each other in W0.
- The linter rule (W7).
- The W1 extension of `erp-v2-check-coverage` to
  cross-module coverage.

### What we lose

- v1's flat layout. The new layout has more directories; the
  benefit is locality and a single internal pattern across
  modules. v1's flatness was the cheapest possible layout
  and the most expensive to navigate.
