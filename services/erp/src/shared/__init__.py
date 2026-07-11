"""Shared cross-cutting utilities for ERP v2.

Modules:

- `errors`: error envelope, exception types, FastAPI exception handlers.
- `schemas`: Pydantic v2 base models (TenantModel, MoneyDecimal, etc.).
- `db`: SQLAlchemy async engine, session factory, Base, `set_tenant_context`.
- `idempotency`: `DbIdempotencyStore` + FastAPI middleware.
- `tenant`: `TenantContext` and the `get_tenant_id` dep.
- `time`: UTC helpers.

Import submodules directly: `from shared.errors import ApiError`, etc.
"""
