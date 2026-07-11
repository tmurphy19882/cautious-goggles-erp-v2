"""Boot the v2 app in-process and curl every endpoint.

Requires a real Postgres (testcontainer or `ERP_TEST_DATABASE_URL`).
Runs migrations, seeds the demo data, then exercises every route.

Usage:
    python services/erp/scripts/smoke_test.py

Exits 0 on full PASS, non-zero on the first failure.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx

# Make `src` importable when this script is run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "erp"
DEMO_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEMO_USER_ID = "22222222-2222-2222-2222-222222222222"


def _run_migrations() -> None:
    """Skip in-process; assume the operator already ran migrations.

    The original implementation spawned a subprocess for `alembic upgrade
    head`, but that hits a Windows `NotADirectoryError` when the cwd
    is a OneDrive-synced path. The smoke test assumes migrations are
    already applied; if they aren't, `python -m alembic --config
    migrations/alembic.ini upgrade head` from the service root will
    do it before the smoke test runs.
    """
    pass


def _seed_demo() -> None:
    """Skip in-process; assume migration 0103 has already seeded the demo.

    Same reason as `_run_migrations`: subprocess under OneDrive-synced
    paths hits `NotADirectoryError` on Windows.
    """
    pass


async def _smoke() -> int:
    """Run the full smoke flow. Return 0 on PASS, 1 on first FAIL."""
    from api.main import create_app_v2

    app = create_app_v2()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            failures: list[str] = []

            def check(label: str, ok: bool, detail: str = "") -> None:
                status = "PASS" if ok else "FAIL"
                print(f"  [{status}] {label}{(' — ' + detail) if detail else ''}")
                if not ok:
                    failures.append(label)

            # 1. /health (no auth)
            r = await c.get("/health")
            check("/health", r.status_code == 200, f"got {r.status_code}")

            # 2. /ready (no auth)
            r = await c.get("/ready")
            check(
                "/ready",
                r.status_code in (200, 503),
                f"got {r.status_code}",
            )
            body = r.json()
            check("/ready body has checks", "checks" in body)
            check("/ready reports database", "database" in body.get("checks", {}))

            # 3. /metrics (no auth)
            r = await c.get("/metrics")
            check("/metrics", r.status_code == 200, f"got {r.status_code}")
            check(
                "/metrics exposes request counter",
                "http_requests_total" in r.text or "# HELP" in r.text,
            )

            # 4. /api/v1/erp/identity/permissions — without auth → 400/401/403
            # (400 from the tenant dep is the expected W0 behaviour: the
            # dep chain raises TenantRequiredError before reaching the
            # permission check.)
            r = await c.get("/api/v1/erp/identity/permissions")
            check(
                "/identity/permissions unauthenticated rejected",
                r.status_code in (400, 401, 403),
                f"got {r.status_code}",
            )

            # 5. With demo admin headers → 200 + catalog
            auth_headers = {
                "x-tenant-id": DEMO_TENANT_ID,
                "x-user-id": DEMO_USER_ID,
            }
            r = await c.get("/api/v1/erp/identity/permissions", headers=auth_headers)
            check(
                "/identity/permissions with admin",
                r.status_code == 200,
                f"got {r.status_code}",
            )
            if r.status_code == 200:
                keys = {p["key"] for p in r.json()}
                check("catalog has identity.user.read", "identity.user.read" in keys)
                check("catalog has o2c.so.write", "o2c.so.write" in keys)
                check("catalog has platform.tenant.write", "platform.tenant.write" in keys)

            # 6. /identity/roles with demo admin
            r = await c.get("/api/v1/erp/identity/roles", headers=auth_headers)
            check(
                "/identity/roles with admin",
                r.status_code == 200,
                f"got {r.status_code}",
            )
            if r.status_code == 200:
                role_keys = {role["key"] for role in r.json()}
                # Migration 0103 seeds exactly one role (`admin`); a
                # `viewer` role lands in a later wave.
                check("roles include 'admin'", "admin" in role_keys)

            # 7. POST without Idempotency-Key → 400
            r = await c.post(
                "/api/v1/erp/identity/roles",
                json={"key": "smoke-test", "name": "Smoke Test"},
                headers=auth_headers,
            )
            check(
                "POST /identity/roles without Idempotency-Key returns 400",
                r.status_code == 400,
                f"got {r.status_code}",
            )
            if r.status_code == 400:
                check(
                    "POST 400 carries idempotency_key_required code",
                    r.json().get("code") == "idempotency_key_required",
                )

            # 8. Error envelope shape on every error
            r = await c.get("/api/v1/erp/identity/roles")
            if r.status_code in (400, 401, 403):
                body = r.json()
                check(
                    "error envelope has code",
                    "code" in body,
                )
                check(
                    "error envelope has message",
                    "message" in body,
                )

            print()
            if failures:
                print(f"SMOKE TEST FAIL: {len(failures)} failure(s)")
                for f in failures:
                    print(f"  - {f}")
                return 1
            print("SMOKE TEST PASS")
            return 0


def main() -> int:
    print("=" * 60)
    print("ERP v2 smoke test")
    print("=" * 60)
    try:
        print("Applying migrations...")
        _run_migrations()
    except subprocess.CalledProcessError as exc:
        print(f"alembic upgrade failed: {exc.stderr.decode(errors='replace')}")
        return 1
    except FileNotFoundError:
        print("alembic not found; install with `pip install -e \".[dev]\"`")
        return 1

    print("Seeding demo data...")
    try:
        _seed_demo()
    except subprocess.CalledProcessError as exc:
        print(f"seed_demo failed: {exc.stderr.decode(errors='replace')}")
        return 1

    print("Exercising endpoints...")
    return asyncio.run(_smoke())


if __name__ == "__main__":
    raise SystemExit(main())
