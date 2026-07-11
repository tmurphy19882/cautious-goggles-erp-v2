"""FastAPI deps for identity.

Closes BUG-009 and BUG-010.

BUG-009: `require_permission` no longer replicates the service-account
short-circuit and the frozenset check inline. It delegates to
`PermissionService.assert_can`, so the service-account allow-list fix
(BUG-006) and the metrics increment both run on every gated route.

BUG-010: the dead `from identity.service import PermissionDenied as _PD`
import is gone.
"""
from __future__ import annotations

from typing import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity.service import PermissionDenied, PermissionService, Principal
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

    W0 trusts the `x-user-id` header. W1 replaces this with a real JWT
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

    Delegates to `PermissionService.assert_can` (BUG-009) so the
    service-account allow-list (BUG-006) and the metrics increment
    both run. The dep opens a short-lived session to call the service;
    the route may also open its own session via `Depends(get_db_session)`
    and both sessions coexist (the dep's is closed before the route
    body runs).

    Translates the service's `identity.service.PermissionDenied` to
    `shared.errors.PermissionDeniedError` so the global error envelope
    renders a clean 403.

    Usage:

        @router.post("/sales-orders", dependencies=[Depends(require_permission("erp.so.write"))])
        async def create_so(...): ...
    """

    async def _dep(
        request: Request,
        principal: Principal = Depends(get_current_principal),
    ) -> None:
        session_factory = get_session_factory(request)
        try:
            async with session_factory() as session:
                await PermissionService(session).assert_can(principal, permission_key)
        except PermissionDenied as exc:
            raise PermissionDeniedError(
                f"User lacks permission `{permission_key}`",
                details={"permission_key": permission_key},
            ) from exc

    return _dep
