"""FastAPI deps for identity.

- `get_current_principal` — extract the principal from the request.
  In W0 it accepts a header for testing; W1 will replace this with a
  proper JWT validator (`x-user-id` + `x-tenant-id` + signed claims).
- `require_permission(key)` — returns a dep that asserts the principal
  has `key`. Use as `Depends(require_permission("erp.so.create"))`.
"""
from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity.service import PermissionService, Principal
from shared.errors import PermissionDeniedError, UnauthenticatedError
from shared.tenant import require_tenant_id


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def get_db_session(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> AsyncSession:
    async with session_factory() as session:
        yield session


async def get_current_principal(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
    session: AsyncSession = Depends(get_db_session),
) -> Principal:
    """Resolve the current principal.

    W0 trusts the `x-user-id` header (or returns a synthetic principal
    for service-account paths). W1 replaces this with a real JWT
    validator that pulls `sub` and `tenant_id` from claims.
    """
    user_id_header = request.headers.get("x-user-id")
    if not user_id_header:
        raise UnauthenticatedError("x-user-id header required (W0); W1 swaps to JWT")

    try:
        user_id = UUID(user_id_header)
    except ValueError as exc:
        raise UnauthenticatedError("x-user-id must be a UUID") from exc

    svc = PermissionService(session)
    principal = await svc.load_principal(user_id=user_id, tenant_id=tenant_id)
    if principal is None:
        raise UnauthenticatedError("user not found or inactive")
    return principal


def require_permission(permission_key: str) -> Callable[..., Awaitable[None]]:
    """Build a FastAPI dep that asserts the principal has `permission_key`.

    Usage:

        @router.post("/sales-orders", dependencies=[Depends(require_permission("erp.so.write"))])
        async def create_so(...): ...
    """

    async def _dep(principal: Principal = Depends(get_current_principal)) -> None:
        # Re-open a session for the assertion so the dep stays clean even
        # if the route opens its own session.
        from identity.service import PermissionDenied as _PD  # avoid circular

        # Use the principal's pre-fetched keys; assert_can here would
        # double-query the DB. We replicate the logic but with metrics.
        if not principal.is_service_account:
            keys = principal.permission_keys or frozenset()
            if permission_key not in keys:
                from observability.metrics import metrics

                metrics().permission_denials_total.labels(
                    permission_key=permission_key, route="-"
                ).inc()
                raise PermissionDeniedError(
                    f"User lacks permission `{permission_key}`",
                    details={"permission_key": permission_key},
                )

    return _dep
