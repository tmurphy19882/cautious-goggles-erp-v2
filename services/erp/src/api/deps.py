"""FastAPI deps for the v2 app — re-exports of the building blocks used
by every module's API router.

This module exists to give per-module API files a single, stable
import path (`from api.deps import ...`) without forcing each one
to know whether the dep lives in `identity`, `shared.tenant`, or
somewhere else.

If you add a new dep that more than one module needs, put it here.

Closes P1-1 (2026-07-11 audit): the W7-W10 API files all imported
`from api.deps import get_session, get_tenant_id, get_user_id` but
the module didn't exist, breaking the entire v2 app at import time.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request

from identity.deps import get_db_session, get_session_factory
from shared.tenant import current_tenant_id, require_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession


# ----- session ------------------------------------------------------------- #


async def get_session(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncSession:
    """Yield an `AsyncSession` for the request.

    Re-exports `identity.deps.get_db_session` so the W7-W10 routers
    can use the same short name (`Depends(get_session)`) that the
    W0-W5 routers already use internally. Keeping the name short
    keeps the W7-W10 source unchanged.
    """
    return session


# ----- tenant id ------------------------------------------------------------ #


async def get_tenant_id(
    request: Request,
) -> UUID:
    """Return the current tenant id, raising if absent.

    Re-exports `shared.tenant.require_tenant_id` so callers can
    import from a single place.
    """
    tid = current_tenant_id(request)
    if tid is None:
        # Delegate to the canonical dep so the error envelope matches.
        return await require_tenant_id(request)
    return tid


# ----- user id -------------------------------------------------------------- #


async def get_user_id(request: Request) -> UUID:
    """Return the current user id from the `x-user-id` header.

    The W0 platform trusts the header. W1 replaces this with a real
    JWT validator that pulls `sub` from claims; this dep is a thin
    shim that keeps the W7-W10 routers stable through that swap.
    """
    from shared.errors import UnauthenticatedError

    raw = request.headers.get("x-user-id")
    if not raw:
        raise UnauthenticatedError("x-user-id header required (W0); W1 swaps to JWT")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise UnauthenticatedError("x-user-id must be a UUID") from exc


__all__ = [
    "get_session",
    "get_tenant_id",
    "get_user_id",
    "get_session_factory",
    "get_db_session",
]
