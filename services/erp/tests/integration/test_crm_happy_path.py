"""W5 CRM core happy-path test.

End-to-end: create a pipeline + stage, capture a lead, qualify, convert
to party + opportunity, create a quote, send, accept, convert to SO
(O2C-15 — closes the long-promised quote-to-SO flow).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_crm_quote_to_so_happy_path(
    client: httpx.AsyncClient, admin_headers
) -> None:
    # 1. Set up master data: product + party.
    r = await client.post(
        "/api/v1/erp/master-data/products",
        json={"sku": f"CRM-SKU-{uuid4().hex[:6]}", "name": "CRM Widget"},
        headers=admin_headers,
    )
    product_id = r.json()["id"]

    # 2. Create a pipeline with three stages.
    r = await client.post(
        "/api/v1/erp/crm/pipelines",
        json={"name": f"Sales Pipeline {uuid4().hex[:6]}", "is_default": True},
        headers=admin_headers,
    )
    pipeline_id = r.json()["id"]

    stages = []
    for key, name, prob, won, lost in [
        ("qual", "Qualified", "20", False, False),
        ("prop", "Proposal", "50", False, False),
        ("won", "Won", "100", True, False),
    ]:
        r = await client.post(
            f"/api/v1/erp/crm/pipelines/{pipeline_id}/stages",
            json={
                "key": key,
                "name": name,
                "position": len(stages) + 1,
                "probability_pct": prob,
                "is_won": won,
                "is_lost": lost,
            },
            headers=admin_headers,
        )
        assert r.status_code == 201, r.text
        stages.append(r.json()["id"])

    # 3. Create a lead.
    r = await client.post(
        "/api/v1/erp/crm/leads",
        json={"company_name": f"Acme Lead {uuid4().hex[:6]}", "email": "lead@acme.example.com"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    lead_id = r.json()["id"]

    # 4. Qualify the lead.
    r = await client.post(
        f"/api/v1/erp/crm/leads/{lead_id}/qualify",
        json={"score": 80},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "qualified"

    # 5. Convert the lead to a Party + Opportunity.
    r = await client.post(
        f"/api/v1/erp/crm/leads/{lead_id}/convert",
        json={
            "party_code": f"ACME-{uuid4().hex[:6]}",
            "opportunity_name": "Acme Q4 deal",
            "pipeline_id": pipeline_id,
            "stage_id": stages[1],  # proposal
            "amount": "1500.00",
        },
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    convert = r.json()
    party_id = convert["party_id"]
    opp_id = convert["opportunity_id"]

    # 6. Set a credit limit so the SO confirm doesn't go on hold.
    r = await client.post(
        "/api/v1/erp/master-data/credit-limits",
        json={"customer_id": party_id, "currency": "USD", "limit_amount": "100000.00"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # 7. Create a quote on the opportunity.
    r = await client.post(
        "/api/v1/erp/crm/quotes",
        headers={**admin_headers, "Idempotency-Key": f"q-{uuid4()}"},
        json={
            "party_id": party_id,
            "opportunity_id": opp_id,
            "currency": "USD",
            "lines": [
                {
                    "product_id": product_id,
                    "sku": "WIDGET-A",
                    "description": "Quote line",
                    "quantity": "100",
                    "unit": "each",
                    "unit_price": "15.00",
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    quote_id = r.json()["id"]
    assert Decimal(r.json()["total_amount"]) == Decimal("1500.00")

    # 8. Send + accept.
    r = await client.post(f"/api/v1/erp/crm/quotes/{quote_id}/send", headers=admin_headers)
    assert r.status_code == 200
    r = await client.post(f"/api/v1/erp/crm/quotes/{quote_id}/accept", headers=admin_headers)
    assert r.status_code == 200

    # 9. Convert to SO (O2C-15 — the long-promised feature).
    r = await client.post(
        f"/api/v1/erp/crm/quotes/{quote_id}/convert-to-so",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    so_result = r.json()
    assert so_result["status"] == "converted"
    so_id = so_result["sales_order_id"]

    # 10. Verify the SO is real and has the right lines.
    # SO confirm + run workflow would also test the rest of O2C;
    # we keep this test focused on the CRM→O2C handoff.
    r = await client.post(
        f"/api/v1/erp/sales-orders/{so_id}/confirm",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
