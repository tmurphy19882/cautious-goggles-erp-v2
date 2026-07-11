"""PermissionService — the single chokepoint for authorization.

Two entry points:

- `assert_can(principal, permission_key)`: raises `PermissionDenied` if not.
- `can(principal, permission_key) -> bool`: non-raising.

Permission keys are dotted strings like `erp.so.create`, `erp.so.confirm`,
`erp.so.cancel`, `erp.invoice.write`, `erp.party.read`. The catalog is
seeded in the `0100_identity` migration.

Per-tenant roles and role-permission grants are seeded in W6. Until then,
the `PermissionService` correctly answers "no" for everything *except*
`identity.*` permissions if a future-wave seed grants them. W0 only
guarantees the service + tables are present; the per-tenant seeding
land with W6's tenant-onboarding flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from identity.models import Role, RolePermission, User
from observability.metrics import metrics


class PermissionDenied(Exception):
    """Raised by `assert_can` when a principal lacks a permission."""

    def __init__(self, permission_key: str, *, principal_id: UUID | None = None) -> None:
        super().__init__(f"permission denied: {permission_key}")
        self.permission_key = permission_key
        self.principal_id = principal_id


@dataclass(slots=True)
class Principal:
    """Authenticated user/service account. Built from JWT in W1+."""

    user_id: UUID
    tenant_id: UUID
    is_service_account: bool = False
    # Pre-fetched permission keys; if None, the service will hit the DB.
    permission_keys: frozenset[str] | None = None
    extra: dict[str, str] = field(default_factory=dict)


class PermissionService:
    """Async authorization checks. Stateless aside from a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_principal(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Principal | None:
        """Load a principal from the DB, including their effective permissions."""
        stmt = (
            select(User)
            .where(User.id == user_id, User.tenant_id == tenant_id, User.deleted_at.is_(None))
        )
        user = (await self._session.execute(stmt)).scalar_one_or_none()
        if user is None or not user.is_active:
            return None

        # Effective permissions = union of all permissions across the user's roles.
        perm_keys: set[str] = set()
        for role in user.roles:
            if role.deleted_at is not None:
                continue
            stmt_perms = select(RolePermission.permission_key).where(RolePermission.role_id == role.id)
            rows = await self._session.execute(stmt_perms)
            perm_keys.update(r[0] for r in rows.all())

        return Principal(
            user_id=user.id,
            tenant_id=user.tenant_id,
            is_service_account=user.is_service_account,
            permission_keys=frozenset(perm_keys),
        )

    async def can(self, principal: Principal, permission_key: str) -> bool:
        if principal.is_service_account:
            # Service accounts have *all* permissions by default. W6's tenant
            # onboarding may add an explicit allow/deny list per connector.
            return True
        keys = principal.permission_keys
        if keys is None:
            keys = await self._load_keys(principal)
        return permission_key in keys

    async def assert_can(self, principal: Principal, permission_key: str) -> None:
        if not await self.can(principal, permission_key):
            metrics().permission_denials_total.labels(
                permission_key=permission_key, route="-"
            ).inc()
            raise PermissionDenied(permission_key, principal_id=principal.user_id)

    async def grant_to_role(
        self,
        *,
        role: Role,
        permission_keys: Iterable[str],
    ) -> None:
        """Add `permission_keys` to `role`. Idempotent."""
        for key in permission_keys:
            stmt = select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_key == key,
            )
            existing = (await self._session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                self._session.add(RolePermission(role_id=role.id, permission_key=key))
        await self._session.flush()

    async def _load_keys(self, principal: Principal) -> frozenset[str]:
        stmt = (
            select(RolePermission.permission_key)
            .join(Role, Role.id == RolePermission.role_id)
            .join(User.roles)
            .where(User.id == principal.user_id, User.tenant_id == principal.tenant_id)
        )
        rows = await self._session.execute(stmt)
        return frozenset(r[0] for r in rows.all())
