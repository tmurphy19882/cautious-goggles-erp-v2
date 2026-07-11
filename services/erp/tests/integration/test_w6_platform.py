"""W6 platform happy-path test.

- Provision a new tenant.
- Create a webhook subscription.
- Create a payment intent.
- Set a feature flag.
- Create a role with permissions.
- Assign the role to a user.
"""
from __future__ import annotations

from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_w6_tenant_provisioning_and_rbac(
    client: httpx.AsyncClient, admin_headers
) -> None:
    # 1. Provision a fresh tenant.
    slug = f"test-{uuid4().hex[:6]}"
    r = await client.post(
        "/api/v1/erp/platform/tenants",
        json={"name": f"Test Tenant {slug}", "slug": slug, "admin_email": f"admin-{slug}@example.com"},
    )
    assert r.status_code == 201, r.text
    tenant = r.json()
    tenant_id = tenant["tenant_id"]
    assert "admin_user_id" in tenant

    # Re-provisioning the same slug is idempotent.
    r2 = await client.post(
        "/api/v1/erp/platform/tenants",
        json={"name": "Test Tenant (dup)", "slug": slug},
    )
    assert r2.status_code == 201
    assert r2.json()["idempotent"] is True
    assert r2.json()["tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_w6_feature_flag_and_rbac_write(
    client: httpx.AsyncClient, admin_headers
) -> None:
    # 1. Set a feature flag.
    r = await client.put(
        "/api/v1/erp/platform/feature-flags/erp.v2.o2c.enabled",
        json={"enabled": True, "variant": "on"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text

    # 2. Read it back.
    r = await client.get(
        "/api/v1/erp/platform/feature-flags/erp.v2.o2c.enabled",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is True

    # 3. Create a custom role with a subset of permissions.
    r = await client.post(
        "/api/v1/erp/platform/roles",
        json={
            "key": "sales-manager",
            "name": "Sales Manager",
            "description": "Can manage SOs but not invoices",
            "permission_keys": [
                "o2c.so.read",
                "o2c.so.write",
                "o2c.so.confirm",
                "master.party.read",
            ],
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]

    # 4. Create a user to assign the role to.
    r = await client.post(
        "/api/v1/erp/master-data/customers",  # creates a non-customer user for the test
        json={"email": f"sm-{uuid4()}@example.com"},
        headers=admin_headers,
    )
    # (master-data/customers creates a User; the role assignment uses user_id)
    user_id = r.json()["id"]

    # 5. Assign the role to the user.
    r = await client.post(
        f"/api/v1/erp/platform/users/{user_id}/roles",
        json={"role_id": role_id},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_w6_payment_intent(
    client: httpx.AsyncClient, admin_headers
) -> None:
    # Need a customer to attach the intent to.
    r = await client.post(
        "/api/v1/erp/master-data/customers",
        json={"email": f"pay-{uuid4()}@example.com"},
        headers=admin_headers,
    )
    customer_id = r.json()["id"]

    r = await client.post(
        "/api/v1/erp/platform/payments/intents",
        json={"customer_id": customer_id, "amount": "100.00", "currency": "USD", "method": "card"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    intent = r.json()
    assert "intent_id" in intent
    assert intent["status"] in ("succeeded", "requires_action")
    # W6 stub: amount divisible by 100 → succeeded.
    assert intent["status"] == "succeeded"


@pytest.mark.asyncio
async def test_w6_webhook_subscription(
    client: httpx.AsyncClient, admin_headers
) -> None:
    r = await client.post(
        "/api/v1/erp/platform/webhooks",
        json={
            "name": "Test hook",
            "url": "http://localhost:9999/hook",
            "events": ["scm.erp.order-confirmed.v1"],
            "secret": "test-secret",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    r = await client.get(
        "/api/v1/erp/platform/webhooks",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert any(w["name"] == "Test hook" for w in r.json()["items"])
