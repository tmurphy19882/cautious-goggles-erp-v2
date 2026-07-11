"""Seed the demo tenant + admin user + role grants (idempotent).

Mirrors migration `0103_seed_demo.py` so the demo data exists even on
a DB that was migrated to a revision before 0103 (e.g. an existing v1
DB that v2 is taking over). Idempotent — re-running is a no-op.

Constants (must match 0103_seed_demo.py):
  - tenant_id  = 11111111-1111-1111-1111-111111111111
  - user_id    = 22222222-2222-2222-2222-222222222222
  - admin role = 33333333-3333-3333-3333-333333333333
  - viewer     = 44444444-4444-4444-4444-444444444444

Usage:
    python services/erp/scripts/seed_demo.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

# Make `src` importable when this script is run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from shared.db import create_engine, create_session_factory, set_tenant_context  # noqa: E402
from shared.schemas import utcnow  # noqa: E402


TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ADMIN_ROLE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
VIEWER_ROLE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


async def seed() -> None:
    engine = create_engine()
    sf = create_session_factory(engine)
    try:
        async with sf() as session:
            await set_tenant_context(session, TENANT_ID)
            # Idempotent inserts via ON CONFLICT DO NOTHING.
            await session.execute(
                text("""
                    INSERT INTO users (id, tenant_id, email, display_name, is_active, is_service_account)
                    VALUES (:id, :tid, 'admin@acme.com', 'Acme Admin', true, false)
                    ON CONFLICT (id) DO NOTHING
                """),
                {"id": str(USER_ID), "tid": str(TENANT_ID)},
            )
            await session.execute(
                text("""
                    INSERT INTO roles (id, tenant_id, key, name, description, is_system)
                    VALUES (:id, :tid, 'admin', 'Administrator',
                            'Full access to every permission in the catalog', true)
                    ON CONFLICT (id) DO NOTHING
                """),
                {"id": str(ADMIN_ROLE_ID), "tid": str(TENANT_ID)},
            )
            await session.execute(
                text("""
                    INSERT INTO roles (id, tenant_id, key, name, description, is_system)
                    VALUES (:id, :tid, 'viewer', 'Viewer',
                            'Read-only access (every *.read permission)', true)
                    ON CONFLICT (id) DO NOTHING
                """),
                {"id": str(VIEWER_ROLE_ID), "tid": str(TENANT_ID)},
            )
            await session.execute(
                text("""
                    INSERT INTO user_roles (user_id, role_id, granted_by)
                    VALUES (:uid, :rid, NULL)
                    ON CONFLICT (user_id, role_id) DO NOTHING
                """),
                {"uid": str(USER_ID), "rid": str(ADMIN_ROLE_ID)},
            )
            await session.execute(
                text("""
                    INSERT INTO role_permissions (role_id, permission_key)
                    SELECT :rid, key FROM permissions
                    ON CONFLICT DO NOTHING
                """),
                {"rid": str(ADMIN_ROLE_ID)},
            )
            await session.execute(
                text("""
                    INSERT INTO role_permissions (role_id, permission_key)
                    SELECT :rid, key FROM permissions WHERE action = 'read'
                    ON CONFLICT DO NOTHING
                """),
                {"rid": str(VIEWER_ROLE_ID)},
            )
            await session.commit()
        print("Seed complete:")
        print(f"  tenant_id    = {TENANT_ID}")
        print(f"  admin user   = {USER_ID} (admin@acme.com)")
        print(f"  admin role   = {ADMIN_ROLE_ID}")
        print(f"  viewer role  = {VIEWER_ROLE_ID}")
        print(f"  timestamp    = {utcnow().isoformat()}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
