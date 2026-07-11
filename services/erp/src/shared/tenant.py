"""Tenant context for ERP v2.

Closes BUG-008: there used to be three sources of truth for the current
tenant, and they disagreed on type. The contract is now:

  - `ObservabilityMiddleware` parses the `x-tenant-id` header to a UUID
    once and stores `UUID | None` on `request.state.tenant_id`.
  - `current_tenant_id(request) -> UUID | None` returns it.
  - `require_tenant_id` raises `TenantRequiredError` if absent, else
    returns the UUID.
  - `TenantId = Depends(require_tenant_id)` is the FastAPI dep.

All callers should use `Depends(require_tenant_id)` (or the alias) so
the type is correct end-to-end.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request

from shared.errors import TenantRequiredError


def current_tenant_id(request: Request) -> UUID | None:
    """Read tenant id from request state. Set by `ObservabilityMiddleware`."""
    return getattr(request.state, "tenant_id", None)


def set_tenant_id(request: Request, tenant_id: UUID | None) -> None:
    """Set tenant id on request state. Called by the middleware after parsing
    the `x-tenant-id` header. Tests can call this to set the tenant
    without going through HTTP.
    """
    request.state.tenant_id = tenant_id


async def require_tenant_id(request: Request) -> UUID:
    """FastAPI dep: tenant id is required for this route."""
    tid = current_tenant_id(request)
    if tid is None:
        raise TenantRequiredError("x-tenant-id header required")
    return tid


# Convenience alias to use in route signatures:
#     tenant_id: UUID = Depends(require_tenant_id)
TenantId = Depends(require_tenant_id)
