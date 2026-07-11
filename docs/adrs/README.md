# Architectural Decision Records — ERP v2

ADRs that govern v2. Each is a one-page `NNNN-title.md` with: Context, Decision, Consequences.

| # | Title | Status |
|---|-------|--------|
| 0001 | Wave-based delivery, no big-bang cutover | **Accepted** |
| 0002 | Pydantic v2 + SQLAlchemy 2.0 async + Alembic | **Accepted** |
| 0003 | Temporal for O2C and P2P sagas | **Accepted** |
| 0004 | Outbox pattern + Avro via Apicurio for every event | **Accepted** |
| 0005 | Every mutating route gated by `PermissionService` | **Accepted** |
| 0006 | Multiline-by-default for SO and PO (no `body.lines[0]`) | **Accepted** |
| 0007 | Party-centric CRM (no separate CRM service) — inherited from `ADR-0016` | **Accepted** |
| 0008 | Soft delete (`deleted_at`) on every domain table | **Accepted** |
| 0009 | Per-tenant feature flags via Unleash | **Accepted** |
| 0010 | OTel + Prometheus first-class on every handler | **Accepted** |

ADRs are written as each wave's work forces a decision; not all up front.
