"""Pydantic v2 schemas for the identity module.

Read models: `UserRead`, `RoleRead`, `PermissionRead`.
Write models: `RoleCreate` (and more in later waves).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import EmailStr, Field, StringConstraints

from shared.schemas import AppModel, CodeStr, NonEmptyStr, ShortStr


class UserRead(AppModel):
    id: UUID
    tenant_id: UUID
    email: EmailStr
    display_name: str | None
    is_active: bool
    is_service_account: bool
    last_login_at: datetime | None
    created_at: datetime


class RoleCreate(AppModel):
    key: CodeStr
    name: ShortStr
    description: str | None = None
    permission_keys: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(
        default_factory=list,
        description="Permission keys to grant to this role (e.g. `erp.so.create`)",
    )


class RoleRead(AppModel):
    id: UUID
    tenant_id: UUID
    key: str
    name: str
    description: str | None
    is_system: bool
    permission_keys: list[str] = Field(default_factory=list)
    created_at: datetime


class PermissionRead(AppModel):
    key: str
    resource: str
    action: str
    description: str | None
