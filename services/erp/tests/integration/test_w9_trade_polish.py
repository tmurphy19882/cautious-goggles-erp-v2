"""W9 — Trade compliance integration tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from trade.service import FTZService, HTSResolver, ScreeningService


pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------- #
# HTS                                                                     #
# --------------------------------------------------------------------- #


async def test_w9_seed_hts_and_resolve(session, tenant_id, user_id):
    # Seed a few HTS codes for the tenant
    from sqlalchemy import text as _sa_text

    await session.execute(
        _sa_text(
            """
            INSERT INTO hts_codes (tenant_id, code, description, keywords, duty_rate, unit)
            VALUES
              (:tid, '8471.30.0100', 'Portable digital ADP machines', ARRAY['laptop','portable','computer'], 0.0, 'no'),
              (:tid, '8504.40.9550', 'Static converters', ARRAY['converter','power supply'], 0.015, 'no'),
              (:tid, '9405.40.0000', 'LED lamps', ARRAY['lamp','light','led'], 0.035, 'no')
            """
        ),
        {"tid": tenant_id},
    )
    await session.flush()

    resolver = HTSResolver(session)
    matches = await resolver.resolve(
        tenant_id=tenant_id, description="A portable LED lamp for laptops"
    )
    assert len(matches) >= 1
    codes = {m.code for m in matches}
    # Should match the LED lamp first (highest score for 'lamp' / 'led')
    assert "9405.40.0000" in codes


# --------------------------------------------------------------------- #
# FTZ                                                                     #
# --------------------------------------------------------------------- #


async def test_w9_ftz_admit_and_remove(session, tenant_id, user_id):
    svc = FTZService(session)
    fid = await svc.admit(
        tenant_id=tenant_id,
        entry_number="FTZ-001",
        zone_id="Z-1",
        admission_date="2026-07-10",
        hts_code="8471.30.0100",
        quantity=Decimal("100"),
        value=Decimal("50000.00"),
    )
    assert fid is not None

    inv = await svc.weekly_inventory(tenant_id=tenant_id, as_of="2026-07-15")
    assert any(r["entries"] >= 1 for r in inv)

    await svc.remove(tenant_id=tenant_id, entry_id=fid, removal_date="2026-07-20")
    inv = await svc.weekly_inventory(tenant_id=tenant_id, as_of="2026-07-25")
    assert not any(r["entries"] >= 1 for r in inv)


# --------------------------------------------------------------------- #
# Screening                                                               #
# --------------------------------------------------------------------- #


async def test_w9_screening_match(session, tenant_id, user_id):
    from uuid import uuid4
    svc = ScreeningService(session)
    pid = uuid4()
    sid = await svc.screen(
        tenant_id=tenant_id,
        party_id=pid,
        party_name="Acme Bad Corp LLC",  # matches the stub list
    )
    assert sid is not None
    snap = await svc.latest(tenant_id=tenant_id, party_id=pid)
    assert snap is not None
    assert snap["matched"] is True
    assert float(snap["match_score"]) >= 0.9


async def test_w9_screening_no_match(session, tenant_id, user_id):
    from uuid import uuid4
    svc = ScreeningService(session)
    pid = uuid4()
    sid = await svc.screen(
        tenant_id=tenant_id,
        party_id=pid,
        party_name="Totally Innocent Co",
    )
    snap = await svc.latest(tenant_id=tenant_id, party_id=pid)
    assert snap["matched"] is False
