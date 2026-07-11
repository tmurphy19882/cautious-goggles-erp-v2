"""W3 + W4 happy-path test.

W3: create a real party + contact + address + tax id, then search.
W4: open a period, post a balanced manual journal, close the period.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
async def test_w3_party_create_and_search(
    client: httpx.AsyncClient, admin_headers
) -> None:
    # 1. Create a real party.
    r = await client.post(
        "/api/v1/erp/master-data/parties",
        json={
            "code": f"ACME-{uuid4().hex[:6]}",
            "name": "Acme Corporation",
            "kind": "customer",
            "email": "billing@acme.example.com",
            "phone": "+1-555-0100",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    party = r.json()
    party_id = party["id"]

    # 2. Add a contact.
    r = await client.post(
        f"/api/v1/erp/master-data/parties/{party_id}/contacts",
        json={
            "full_name": "Jane Doe",
            "role": "billing",
            "email": "jane@acme.example.com",
            "is_primary": True,
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # 3. Add an address.
    r = await client.post(
        f"/api/v1/erp/master-data/parties/{party_id}/addresses",
        json={
            "kind": "billing",
            "line1": "1 Acme Way",
            "city": "Springfield",
            "region": "IL",
            "postal_code": "62701",
            "country_code": "US",
            "is_primary": True,
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # 4. Add a tax id.
    r = await client.post(
        f"/api/v1/erp/master-data/parties/{party_id}/tax-ids",
        json={"tax_id_type": "ein", "tax_id": "12-3456789", "country_code": "US"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text

    # 5. Search by name (fuzzy).
    r = await client.get(
        "/api/v1/erp/master-data/search",
        params={"q": "Acme"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert any(h["entity_id"] == party_id for h in hits), f"party not in hits: {hits}"

    # 6. List parties by kind.
    r = await client.get(
        "/api/v1/erp/master-data/parties",
        params={"kind": "customer"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert any(p["code"] == party["code"] for p in r.json()["items"])


@pytest.mark.asyncio
async def test_w4_post_journal_and_close_period(
    client: httpx.AsyncClient, admin_headers
) -> None:
    # 1. Create two GL accounts.
    r = await client.post(
        "/api/v1/erp/finance/gl/accounts",
        json={
            "code": "1000-CASH",
            "name": "Cash on Hand",
            "kind": "asset",
            "normal_side": "debit",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    cash = r.json()["id"]

    r = await client.post(
        "/api/v1/erp/finance/gl/accounts",
        json={
            "code": "4000-REV",
            "name": "Service Revenue",
            "kind": "revenue",
            "normal_side": "credit",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    rev = r.json()["id"]

    # 2. Open a period.
    r = await client.post(
        "/api/v1/erp/finance/periods",
        json={"code": "2026-07", "start_date": "2026-07-01", "end_date": "2026-07-31"},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    period_id = r.json()["id"]

    # 3. Post a balanced manual journal.
    r = await client.post(
        "/api/v1/erp/finance/journal-entries",
        json={
            "entry_date": "2026-07-15",
            "source": "manual",
            "description": "Test balanced journal",
            "period_id": period_id,
            "lines": [
                {"account_id": cash, "debit_amount": "1000.00", "credit_amount": "0"},
                {"account_id": rev, "debit_amount": "0", "credit_amount": "1000.00"},
            ],
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    entry = r.json()
    assert Decimal(entry["total_debit"]) == Decimal("1000.00")
    assert Decimal(entry["total_credit"]) == Decimal("1000.00")

    # 4. Try an unbalanced journal — should fail.
    r = await client.post(
        "/api/v1/erp/finance/journal-entries",
        json={
            "entry_date": "2026-07-15",
            "source": "manual",
            "description": "Unbalanced",
            "period_id": period_id,
            "lines": [
                {"account_id": cash, "debit_amount": "100.00", "credit_amount": "0"},
                {"account_id": rev, "debit_amount": "0", "credit_amount": "50.00"},
            ],
        },
        headers=admin_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "invalid"

    # 5. Fetch the journal back.
    r = await client.get(
        f"/api/v1/erp/finance/journal-entries/{entry['entry_id']}",
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["lines"]) == 2

    # 6. Close the period.
    approver = uuid4()
    r = await client.post(
        f"/api/v1/erp/finance/periods/{period_id}/close",
        params={"closed_by": str(approver)},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "closed"
