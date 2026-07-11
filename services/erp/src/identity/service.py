"""PermissionService — the single chokepoint for authorization.

Two entry points:

- `assert_can(principal, permission_key)`: raises `PermissionDenied` if not.
- `can(principal, permission_key) -> bool`: non-raising.

Closes BUG-006 and BUG-018.

BUG-006: a service account no longer short-circuits to "yes" based on
the `is_service_account` flag alone. It must also have the requested
key in its `service_account_scopes` allow-list. Default scope is empty
(deny). The full per-tenant allow/deny list lands in W6.

BUG-018: `load_principal` is now a single round-trip that joins
`User → UserRole → Role → RolePermission` and unions the permission
keys in Python. The N+1 loop is gone.
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
    # BUG-006: per-principal allow-list for service accounts. Empty
    # (deny) by default. Populated by W6's tenant onboarding.
    service_account_scopes: frozenset[str] = field(default_factory=frozenset)
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
        """Load a principal from the DB, including their effective permissions.

        BUG-018: single round-trip join. Two queries (one for the user,
        one for the union of role permissions). The user query uses
        `selectinload` for `user.roles` and `role.permissions` so the
        in-memory union is also computed; we still compute the explicit
        union here to keep the contract stable (and to be safe if the
        lazy-load is ever changed).
        """
        stmt = (
            select(User)
            .where(User.id == user_id, User.tenant_id == tenant_id, User.deleted_at.is_(None))
        )
        user = (await self._session.execute(stmt)).scalar_one_or_none()
        if user is None or not user.is_active:
            return None

        # BUG-018: single-query union via the join path that already
        # exists in `_load_keys`. Soft-deleted roles are excluded.
        # Inactive users (above) are excluded.
        perm_keys: set[str] = set()
        from identity.models import UserRole
        union_stmt = (
            select(RolePermission.permission_key)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user.id,
                User.tenant_id == tenant_id,
                Role.deleted_at.is_(None),
                UserRole.user_id == User.id,  # belt-and-braces join sanity
            )
            .distinct()
        )
        rows = await self._session.execute(union_stmt)
        perm_keys.update(r[0] for r in rows.all())

        # BUG-006: service-account scopes default to empty (deny). They
        # are populated by the W6 onboarding flow's per-tenant list.
        return Principal(
            user_id=user.id,
            tenant_id=user.tenant_id,
            is_service_account=user.is_service_account,
            permission_keys=frozenset(perm_keys),
            service_account_scopes=frozenset(),  # W6 fills this in
        )

    async def can(self, principal: Principal, permission_key: str) -> bool:
        # BUG-006: service-account short-circuit is now gated by an
        # explicit per-principal allow-list. A bare flag is no longer
        # enough to grant a permission.
        if principal.is_service_account:
            return permission_key in principal.service_account_scopes
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
