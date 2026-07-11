"""W8 — HR + Legal integration tests."""
from __future__ import annotations

import pytest

from hr.service import ContractService, ESignService, HRService, render_template


pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------- #
# HR                                                                      #
# --------------------------------------------------------------------- #


async def test_w8_create_department(session, tenant_id, user_id):
    svc = HRService(session)
    did = await svc.create_department(tenant_id=tenant_id, name="Engineering", code="ENG")
    assert did is not None


async def test_w8_create_employee(session, tenant_id, user_id):
    svc = HRService(session)
    eid = await svc.create_employee(
        tenant_id=tenant_id,
        employee_number="E-001",
        first_name="Test",
        last_name="User",
        email="test@example.com",
    )
    assert eid is not None


async def test_w8_payroll_export(session, tenant_id, user_id):
    svc = HRService(session)
    # Need at least one active employee
    await svc.create_employee(
        tenant_id=tenant_id, employee_number="E-002", first_name="A", last_name="B",
        email="ab@example.com",
    )
    pid = await svc.export_payroll(
        tenant_id=tenant_id, period_start="2026-01-01", period_end="2026-01-31"
    )
    assert pid is not None


# --------------------------------------------------------------------- #
# Legal                                                                   #
# --------------------------------------------------------------------- #


def test_w8_template_render():
    out = render_template(
        "Hi {{name}}, welcome to {{company}}.",
        {"name": "Trevor", "company": "Acme"},
    )
    assert out == "Hi Trevor, welcome to Acme."


def test_w8_template_render_missing_var_raises():
    import pytest
    with pytest.raises(ValueError, match="missing template variable"):
        render_template("Hi {{name}}.", {})


async def test_w8_create_template_and_contract(session, tenant_id, user_id):
    cs = ContractService(session)
    tid = await cs.create_template(
        tenant_id=tenant_id,
        name="NDA Standard",
        kind="nda",
        body_md="This NDA is between {{party_a}} and {{party_b}} effective {{date}}.",
        variables=["party_a", "party_b", "date"],
    )
    assert tid is not None

    cid = await cs.create_contract(
        tenant_id=tenant_id,
        template_id=tid,
        title="NDA - Acme / Globex",
        kind="nda",
        variables={"party_a": "Acme", "party_b": "Globex", "date": "2026-07-10"},
    )
    assert cid is not None

    # Read the rendered body
    from sqlalchemy import text as _sa_text
    row = (
        await session.execute(
            _sa_text(
                "SELECT rendered_body_md, status FROM contracts WHERE id = :id"
            ),
            {"id": cid},
        )
    ).first()
    assert row is not None
    assert "Acme" in row[0]
    assert row[1] == "draft"


async def test_w8_contract_state_machine(session, tenant_id, user_id):
    cs = ContractService(session)
    tid = await cs.create_template(
        tenant_id=tenant_id, name="MSA", kind="msa", body_md="X", variables=[]
    )
    cid = await cs.create_contract(
        tenant_id=tenant_id, template_id=tid, title="MSA", kind="msa", variables={}
    )

    # draft -> sent
    await cs.transition(tenant_id=tenant_id, contract_id=cid, target="sent")
    # sent -> signed
    await cs.transition(tenant_id=tenant_id, contract_id=cid, target="signed")
    # signed -> active
    await cs.transition(tenant_id=tenant_id, contract_id=cid, target="active")
    # active -> expired
    await cs.transition(tenant_id=tenant_id, contract_id=cid, target="expired")
    # expired -> active (should fail)
    import pytest
    with pytest.raises(ValueError, match="invalid transition"):
        await cs.transition(tenant_id=tenant_id, contract_id=cid, target="active")


async def test_w8_esign_envelope_signed_marks_contract(session, tenant_id, user_id):
    cs = ContractService(session)
    es = ESignService(session)
    tid = await cs.create_template(
        tenant_id=tenant_id, name="NDA", kind="nda", body_md="X", variables=[]
    )
    cid = await cs.create_contract(
        tenant_id=tenant_id, template_id=tid, title="NDA", kind="nda", variables={}
    )
    # Move to sent
    await cs.transition(tenant_id=tenant_id, contract_id=cid, target="sent")
    # Create envelope (this also re-sets status to sent — but the test
    # shows the e-sign flow can mark the contract signed via the
    # 'signed' event.)
    eid = await es.create_envelope(tenant_id=tenant_id, contract_id=cid)
    await es.record_event(
        tenant_id=tenant_id, envelope_id=eid, event_type="signed", signer_email="x@y.com"
    )
    from sqlalchemy import text as _sa_text
    status = (
        await session.execute(
            _sa_text("SELECT status FROM contracts WHERE id = :id"), {"id": cid}
        )
    ).scalar_one()
    assert status == "signed"
