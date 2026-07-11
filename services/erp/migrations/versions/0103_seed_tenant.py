"""Seed a sample tenant + admin user + admin role with all 65 permissions.

Revision ID: 0103_seed_tenant
Revises: 0102_idempotency_keys
Create Date: 2026-07-11

Wave 0 — Foundations.

This is the *demo* / smoke-test tenant. Production tenants are created
by the W6 onboarding flow. Both paths use the same `provision_erp_tenant`
pluggable; this migration is the canned call.

Fixed UUIDs (so tests and the smoke script can refer to them without a
discovery round-trip):

- Tenant: `11111111-1111-1111-1111-111111111111`
- Admin user: `22222222-2222-2222-2222-222222222222`
- Admin role: `33333333-3333-3333-3333-333333333333`

Idempotent: every INSERT uses `ON CONFLICT DO NOTHING` so re-running
the migration is safe.

NOTE: BUG-006 is partially addressed here — the admin user is a
*regular* user (`is_service_account = false`). Service-account
short-circuit in `PermissionService.can` is gated by a per-principal
allow-list starting in W6.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0103_seed_tenant"
down_revision: str | Sequence[str] | None = "0102_idempotency_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Fixed UUIDs — see module docstring.
TENANT_ID = "11111111-1111-1111-1111-111111111111"
ADMIN_USER_ID = "22222222-2222-2222-2222-222222222222"
ADMIN_ROLE_ID = "33333333-3333-3333-3333-333333333333"


def upgrade() -> None:
    # Tenant row (no separate tenants table in W0; users/roles carry
    # tenant_id directly). We rely on the RLS GUC being set to this
    # sentinel for the inserts below; otherwise RLS will block them.
    conn = op.get_bind()
    conn.execute(
        sa.text(f"SET LOCAL app.tenant_id = '{TENANT_ID}'")
    )

    # 1. Admin user
    op.execute(
        f"""
        INSERT INTO users (
            id, tenant_id, email, display_name, is_active, is_service_account
        ) VALUES (
            '{ADMIN_USER_ID}', '{TENANT_ID}',
            'admin@example.com', 'Demo Admin',
            true, false
        )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # 2. Admin role
    op.execute(
        f"""
        INSERT INTO roles (
            id, tenant_id, key, name, description, is_system
        ) VALUES (
            '{ADMIN_ROLE_ID}', '{TENANT_ID}',
            'admin', 'Administrator',
            'Demo tenant admin — every permission granted', true
        )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # 3. Grant every catalog permission to the admin role.
    # Pull the keys from the catalog so this stays in sync with 0100.
    permission_rows = conn.execute(
        sa.text("SELECT key FROM permissions")
    ).fetchall()
    for (perm_key,) in permission_rows:
        op.execute(
            f"""
            INSERT INTO role_permissions (role_id, permission_key)
            VALUES ('{ADMIN_ROLE_ID}', '{perm_key}')
            ON CONFLICT DO NOTHING
            """
        )

    # 4. Link the admin user to the admin role.
    op.execute(
        f"""
        INSERT INTO user_roles (user_id, role_id, granted_by)
        VALUES ('{ADMIN_USER_ID}', '{ADMIN_ROLE_ID}', '{ADMIN_USER_ID}')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(f"SET LOCAL app.tenant_id = '{TENANT_ID}'"))
    op.execute(f"DELETE FROM user_roles WHERE user_id = '{ADMIN_USER_ID}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_id = '{ADMIN_ROLE_ID}'")
    op.execute(f"DELETE FROM roles WHERE id = '{ADMIN_ROLE_ID}'")
    op.execute(f"DELETE FROM users WHERE id = '{ADMIN_USER_ID}'")
