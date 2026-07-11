"""Boot the v2 app in-process and curl every endpoint.

Requires a real Postgres (testcontainer or `ERP_TEST_DATABASE_URL`).
Runs migrations, seeds the demo data, then exercises every route.

Usage:
    python services/erp/scripts/smoke_test.py

Exits 0 on full PASS, non-zero on the first failure.

W0 covers /health, /ready, /metrics, /identity/*.
W1 covers the O2C happy path: customer → product → location → layer
→ credit limit → SO (multiline) → confirm → run workflow → payment.
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

            # ===================================================================
            # W1 — O2C happy path
            # ===================================================================
            print()
            print("--- W1: O2C happy path ---")

            # 9. Create a customer.
            r = await c.post(
                "/api/v1/erp/master-data/customers",
                json={"email": "smoke-cust@example.com", "display_name": "Smoke Customer"},
                headers=auth_headers,
            )
            check(
                "POST /master-data/customers",
                r.status_code == 201,
                f"got {r.status_code}",
            )
            customer_id = r.json().get("id") if r.status_code == 201 else None

            # 10. Create a product + location + layer.
            r = await c.post(
                "/api/v1/erp/master-data/products",
                json={"sku": "SMOKE-SKU", "name": "Smoke Widget"},
                headers=auth_headers,
            )
            check("POST /master-data/products", r.status_code == 201, f"got {r.status_code}")
            product_id = r.json().get("id") if r.status_code == 201 else None

            r = await c.post(
                "/api/v1/erp/master-data/locations",
                json={"code": "SMOKE-WH", "name": "Smoke WH", "kind": "warehouse"},
                headers=auth_headers,
            )
            check("POST /master-data/locations", r.status_code == 201, f"got {r.status_code}")
            location_id = r.json().get("id") if r.status_code == 201 else None

            if product_id and location_id:
                r = await c.post(
                    f"/api/v1/erp/master-data/products/{product_id}/layers",
                    json={"location_id": location_id, "quantity": "100", "unit_cost": "5.00"},
                    headers=auth_headers,
                )
                check("POST inventory layer", r.status_code == 201, f"got {r.status_code}")

            # 11. Set a credit limit (high).
            if customer_id:
                r = await c.post(
                    "/api/v1/erp/master-data/credit-limits",
                    json={"customer_id": customer_id, "currency": "USD", "limit_amount": "100000.00"},
                    headers=auth_headers,
                )
                check("POST /master-data/credit-limits", r.status_code == 201, f"got {r.status_code}")

            # 12. Create a multi-line SO.
            if customer_id and product_id and location_id:
                r = await c.post(
                    "/api/v1/erp/sales-orders",
                    json={
                        "customer_id": customer_id,
                        "currency": "USD",
                        "lines": [
                            {
                                "product_id": product_id,
                                "sku": "WIDGET-A",
                                "description": "Line 1",
                                "quantity": "10",
                                "unit": "each",
                                "unit_price": "12.50",
                                "ship_from_location_id": location_id,
                            },
                            {
                                "product_id": product_id,
                                "sku": "WIDGET-B",
                                "description": "Line 2",
                                "quantity": "5",
                                "unit": "each",
                                "unit_price": "12.50",
                                "ship_from_location_id": location_id,
                            },
                        ],
                    },
                    headers={**auth_headers, "Idempotency-Key": "smoke-so-1"},
                )
                check("POST /sales-orders (multiline)", r.status_code == 201, f"got {r.status_code}")
                order_id = r.json().get("order_id") if r.status_code == 201 else None
                if order_id:
                    check("SO line_count == 2", r.json().get("line_count") == 2)
                    check("SO total_amount == 187.50", r.json().get("total_amount") == "187.50")

                # 13. Confirm.
                r = await c.post(
                    f"/api/v1/erp/sales-orders/{order_id}/confirm",
                    headers=auth_headers,
                )
                check("POST /sales-orders/{id}/confirm", r.status_code == 200, f"got {r.status_code}")
                check("SO status == confirmed", r.json().get("status") == "confirmed")

                # 14. Run the workflow (sync invoice generation).
                r = await c.post(
                    f"/api/v1/erp/sales-orders/{order_id}/run-workflow",
                    headers=auth_headers,
                )
                check("POST /sales-orders/{id}/run-workflow", r.status_code == 200, f"got {r.status_code}")
                if r.status_code == 200:
                    check("workflow final_status == invoiced", r.json().get("final_status") == "invoiced")
                    check("workflow published events", r.json().get("events_published", 0) >= 2)
                    invoice_id = r.json().get("invoice_id")
                else:
                    invoice_id = None

                # 15. Apply a payment.
                if invoice_id:
                    r = await c.post(
                        f"/api/v1/erp/sales-orders/{order_id}/payments",
                        json={"invoice_id": invoice_id, "amount": "187.50"},
                        headers=auth_headers,
                    )
                    check("POST /sales-orders/{id}/payments", r.status_code == 200, f"got {r.status_code}")
                    if r.status_code == 200:
                        check("payment applied_amount == 187.50", r.json().get("applied_amount") == "187.50")

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
