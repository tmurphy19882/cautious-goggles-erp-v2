"""Tenant context for ERP v2.

Pulls the tenant id from the `x-tenant-id` header (or, when JWT lands in
W1, from the validated JWT's `tenant_id` claim). RLS is the *only*
enforcement; this dep is for ergonomic access in handlers.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request

from shared.errors import TenantRequiredError


def current_tenant_id(request: Request) -> UUID | None:
    """Read tenant id from request state. Set by TenantMiddleware."""
    return getattr(request.state, "tenant_id", None)


async def require_tenant_id(request: Request) -> UUID:
    """FastAPI dep: tenant id is required for this route."""
    tid = current_tenant_id(request)
    if tid is None:
        raise TenantRequiredError("x-tenant-id header required")
    return tid


# Convenience alias to use in route signatures:
#     tenant_id: UUID = Depends(require_tenant_id)
TenantId = Depends(require_tenant_id)
