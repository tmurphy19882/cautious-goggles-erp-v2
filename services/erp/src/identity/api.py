"""Identity read API.

W0 ships only read endpoints (catalog and per-tenant roles).
Write endpoints (role create, role update, permission grant) come in
W6 along with the rest of the platform/admin module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from identity.deps import get_session_factory, require_permission
from identity.models import Permission, Role
from identity.schemas import PermissionRead, RoleRead
from shared.errors import NotFoundError

router = APIRouter(prefix="/identity", tags=["identity"])


def _to_role_read(role: Role) -> RoleRead:
    return RoleRead(
        id=role.id,
        tenant_id=role.tenant_id,
        key=role.key,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permission_keys=[p.key for p in role.permissions],
        created_at=role.created_at,
    )


@router.get(
    "/permissions",
    response_model=list[PermissionRead],
    summary="List the global permission catalog",
)
async def list_permissions(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[PermissionRead]:
    """The catalog is global, not tenant-scoped, so no `x-tenant-id` required."""
    async with session_factory() as session:
        rows = (await session.execute(select(Permission).order_by(Permission.resource, Permission.action))).scalars().all()
    return [
        PermissionRead(key=p.key, resource=p.resource, action=p.action, description=p.description)
        for p in rows
    ]


@router.get(
    "/roles",
    response_model=list[RoleRead],
    summary="List roles for the current tenant",
    dependencies=[Depends(require_permission("identity.role.read"))],
)
async def list_roles(
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> list[RoleRead]:
    tenant_id = request.state.tenant_id
    async with session_factory() as session:
        stmt = (
            select(Role)
            .where(Role.tenant_id == tenant_id, Role.deleted_at.is_(None))
            .options(selectinload(Role.permissions))
            .order_by(Role.name)
        )
        roles = (await session.execute(stmt)).scalars().all()
    return [_to_role_read(r) for r in roles]


@router.get(
    "/roles/{role_id}",
    response_model=RoleRead,
    summary="Get a single role",
    dependencies=[Depends(require_permission("identity.role.read"))],
)
async def get_role(
    role_id: str,
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> RoleRead:
    from uuid import UUID as _UUID

    try:
        rid = _UUID(role_id)
    except ValueError as exc:
        raise NotFoundError(f"role not found: {role_id}") from exc

    tenant_id = request.state.tenant_id
    async with session_factory() as session:
        stmt = (
            select(Role)
            .where(Role.id == rid, Role.tenant_id == tenant_id, Role.deleted_at.is_(None))
            .options(selectinload(Role.permissions))
        )
        role = (await session.execute(stmt)).scalar_one_or_none()
    if role is None:
        raise NotFoundError(f"role not found: {role_id}")
    return _to_role_read(role)
