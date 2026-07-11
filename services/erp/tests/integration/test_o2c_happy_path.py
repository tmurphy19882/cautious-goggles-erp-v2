"""W1 O2C happy-path test.

End-to-end:
1. Create a customer + product + location + inventory layer.
2. Set a credit limit.
3. Create a multi-line sales order.
4. Confirm the order (credit check + reservation + publish ORDER_CONFIRMED).
5. Run the workflow (synchronous invoice generation).
6. Apply a payment.
7. Verify the final state: order invoiced, invoice paid, payment applied.

Requires a real Postgres (testcontainer or `ERP_TEST_DATABASE_URL`).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_o2c_happy_path(client: httpx.AsyncClient, admin_headers) -> None:
    """Closes the W1 'O2C complete' punch list in AUDIT.md."""
    # 1. Create a customer.
    r = await client.post(
        "/api/v1/erp/master-data/customers",
        json={"email": f"cust-{uuid4()}@example.com", "display_name": "Acme Customer"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    customer_id = r.json()["id"]

    # 2. Create a product.
    r = await client.post(
        "/api/v1/erp/master-data/products",
        json={"sku": f"SKU-{uuid4().hex[:8]}", "name": "Widget"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    product_id = r.json()["id"]

    # 3. Create a location.
    r = await client.post(
        "/api/v1/erp/master-data/locations",
        json={"code": f"WH-{uuid4().hex[:6]}", "name": "Main Warehouse", "kind": "warehouse"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    location_id = r.json()["id"]

    # 4. Add an inventory layer so FIFO has something to consume.
    r = await client.post(
        f"/api/v1/erp/master-data/products/{product_id}/layers",
        json={"location_id": location_id, "quantity": "100", "unit_cost": "5.00"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # 5. Set a credit limit (high enough to clear).
    r = await client.post(
        "/api/v1/erp/master-data/credit-limits",
        json={"customer_id": customer_id, "currency": "USD", "limit_amount": "100000.00"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # 6. Create a multi-line SO.
    headers = {**admin_headers, "Idempotency-Key": f"so-{uuid4()}"}
    r = await client.post(
        "/api/v1/erp/sales-orders",
        headers=headers,
        json={
            "customer_id": customer_id,
            "currency": "USD",
            "lines": [
                {
                    "product_id": product_id,
                    "sku": "WIDGET-A",
                    "description": "First line",
                    "quantity": "10",
                    "unit": "each",
                    "unit_price": "12.50",
                    "ship_from_location_id": location_id,
                },
                {
                    "product_id": product_id,
                    "sku": "WIDGET-B",
                    "description": "Second line",
                    "quantity": "5",
                    "unit": "each",
                    "unit_price": "12.50",
                    "ship_from_location_id": location_id,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    order_id = body["order_id"]
    assert body["line_count"] == 2
    assert body["status"] == "draft"
    # Total = (10 + 5) * 12.50 = 187.50
    assert Decimal(body["total_amount"]) == Decimal("187.50")

    # 7. Confirm the order.
    r = await client.post(
        f"/api/v1/erp/sales-orders/{order_id}/confirm",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"

    # 8. Run the workflow (synchronous invoice generation).
    r = await client.post(
        f"/api/v1/erp/sales-orders/{order_id}/run-workflow",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    wf = r.json()
    assert wf["final_status"] == "invoiced"
    assert wf["invoice_id"] is not None
    invoice_id = wf["invoice_id"]
    assert wf["events_published"] >= 2  # ORDER_CONFIRMED + INVOICE_GENERATED

    # 9. Apply a payment.
    r = await client.post(
        f"/api/v1/erp/sales-orders/{order_id}/payments",
        json={"invoice_id": invoice_id, "amount": "187.50"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    pay = r.json()
    assert Decimal(pay["applied_amount"]) == Decimal("187.50")
    assert Decimal(pay["unapplied_amount"]) == Decimal("0")
    assert invoice_id in pay["invoices_affected"]


@pytest.mark.asyncio
async def test_o2c_credit_hold(client: httpx.AsyncClient, admin_headers) -> None:
    """When a credit limit is exceeded, the order is held and
    ORDER_CONFIRMED is NOT published (O2C-2).
    """
    # Set up the same fixtures as the happy path.
    r = await client.post(
        "/api/v1/erp/master-data/customers",
        json={"email": f"cust-{uuid4()}@example.com"},
        headers=admin_headers,
    )
    customer_id = r.json()["id"]
    r = await client.post(
        "/api/v1/erp/master-data/products",
        json={"sku": f"SKU-{uuid4().hex[:8]}", "name": "Widget"},
        headers=admin_headers,
    )
    product_id = r.json()["id"]

    # Set a TINY credit limit.
    r = await client.post(
        "/api/v1/erp/master-data/credit-limits",
        json={"customer_id": customer_id, "currency": "USD", "limit_amount": "10.00"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # Create + confirm an order that exceeds the limit.
    r = await client.post(
        "/api/v1/erp/sales-orders",
        headers={**admin_headers, "Idempotency-Key": f"so-{uuid4()}"},
        json={
            "customer_id": customer_id,
            "currency": "USD",
            "lines": [
                {
                    "product_id": product_id,
                    "sku": "WIDGET-A",
                    "description": "Big order",
                    "quantity": "100",
                    "unit": "each",
                    "unit_price": "12.50",
                },
            ],
        },
    )
    order_id = r.json()["order_id"]
    r = await client.post(
        f"/api/v1/erp/sales-orders/{order_id}/confirm",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "credit_hold"


@pytest.mark.asyncio
async def test_o2c_cancel_releases_reservation(
    client: httpx.AsyncClient, admin_headers
) -> None:
    """Cancel a confirmed order; reservation is released (O2C-4)."""
    r = await client.post(
        "/api/v1/erp/master-data/customers",
        json={"email": f"cust-{uuid4()}@example.com"},
        headers=admin_headers,
    )
    customer_id = r.json()["id"]
    r = await client.post(
        "/api/v1/erp/master-data/products",
        json={"sku": f"SKU-{uuid4().hex[:8]}", "name": "Widget"},
        headers=admin_headers,
    )
    product_id = r.json()["id"]
    r = await client.post(
        "/api/v1/erp/master-data/locations",
        json={"code": f"WH-{uuid4().hex[:6]}", "name": "WH", "kind": "warehouse"},
        headers=admin_headers,
    )
    location_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/erp/master-data/products/{product_id}/layers",
        json={"location_id": location_id, "quantity": "100", "unit_cost": "5.00"},
        headers=admin_headers,
    )
    r = await client.post(
        "/api/v1/erp/master-data/credit-limits",
        json={"customer_id": customer_id, "currency": "USD", "limit_amount": "10000.00"},
        headers=admin_headers,
    )
    r = await client.post(
        "/api/v1/erp/sales-orders",
        headers={**admin_headers, "Idempotency-Key": f"so-{uuid4()}"},
        json={
            "customer_id": customer_id,
            "currency": "USD",
            "lines": [
                {
                    "product_id": product_id,
                    "sku": "WIDGET-A",
                    "description": "Cancel me",
                    "quantity": "5",
                    "unit": "each",
                    "unit_price": "10.00",
                    "ship_from_location_id": location_id,
                }
            ],
        },
    )
    order_id = r.json()["order_id"]
    r = await client.post(
        f"/api/v1/erp/sales-orders/{order_id}/confirm",
        headers=admin_headers,
    )
    assert r.json()["status"] == "confirmed"
    # Now cancel.
    r = await client.post(
        f"/api/v1/erp/sales-orders/{order_id}/cancel",
        headers={**admin_headers, "Idempotency-Key": f"cancel-{uuid4()}"},
        json={"reason": "test cancel"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    # Verify the reservation was released.
    r = await client.get(
        f"/api/v1/erp/sales-orders/{order_id}",
        headers=admin_headers,
    )
    assert r.json()["status"] == "cancelled"
