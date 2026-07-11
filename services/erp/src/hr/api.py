"""W8 — HR + Legal REST routes.

Routes:
- `POST/GET /hr/departments`
- `POST/GET /hr/employees`
- `POST /hr/payroll-exports`
- `POST/GET /legal/contract-templates`
- `POST/GET /legal/contracts`
- `POST /legal/contracts/{id}/transition` (state machine)
- `POST /legal/contracts/{id}/envelopes` (e-sign)
- `POST /legal/envelopes/{id}/events` (e-sign callback)
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, get_tenant_id, get_user_id
from hr.service import ContractService, ESignService, HRService, render_template


router = APIRouter(prefix="", tags=["w8-hr-legal"])


# --------------------------------------------------------------------- #
# HR                                                                      #
# --------------------------------------------------------------------- #


class DepartmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=32)
    parent_id: UUID | None = None


@router.post("/hr/departments", status_code=status.HTTP_201_CREATED)
async def create_department(
    body: DepartmentBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = HRService(session)
    did = await svc.create_department(
        tenant_id=tenant_id, name=body.name, code=body.code, parent_id=body.parent_id
    )
    await session.commit()
    return {"id": str(did)}


@router.get("/hr/departments")
async def list_departments(
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    from sqlalchemy import text as _sa_text

    rows = (
        await session.execute(
            _sa_text(
                "SELECT id, name, code, parent_id, manager_user_id, created_at "
                "FROM departments WHERE tenant_id = :tid ORDER BY name"
            ),
            {"tid": tenant_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


class EmployeeBody(BaseModel):
    employee_number: str = Field(min_length=1, max_length=32)
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=1, max_length=255)
    department_id: UUID | None = None
    title: str | None = None
    hire_date: date | None = None


@router.post("/hr/employees", status_code=status.HTTP_201_CREATED)
async def create_employee(
    body: EmployeeBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = HRService(session)
    eid = await svc.create_employee(
        tenant_id=tenant_id,
        employee_number=body.employee_number,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        department_id=body.department_id,
        title=body.title,
        hire_date=str(body.hire_date) if body.hire_date else None,
    )
    await session.commit()
    return {"id": str(eid)}


@router.get("/hr/employees")
async def list_employees(
    department_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    from sqlalchemy import text as _sa_text

    where = "WHERE tenant_id = :tid"
    params: dict[str, Any] = {"tid": tenant_id}
    if department_id:
        where += " AND department_id = :did"
        params["did"] = department_id
    if status_filter:
        where += " AND status = :status"
        params["status"] = status_filter
    rows = (
        await session.execute(
            _sa_text(
                f"SELECT id, employee_number, first_name, last_name, email, "
                f"department_id, title, hire_date, termination_date, status "
                f"FROM employees {where} ORDER BY last_name, first_name"
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]


class PayrollExportBody(BaseModel):
    period_start: date
    period_end: date
    currency: str = Field(default="USD", min_length=3, max_length=3)


@router.post("/hr/payroll-exports", status_code=status.HTTP_201_CREATED)
async def export_payroll(
    body: PayrollExportBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, Any]:
    svc = HRService(session)
    pid = await svc.export_payroll(
        tenant_id=tenant_id,
        period_start=str(body.period_start),
        period_end=str(body.period_end),
        currency=body.currency,
    )
    await session.commit()
    return {"id": str(pid), "status": "generated"}


# --------------------------------------------------------------------- #
# Legal                                                                   #
# --------------------------------------------------------------------- #


class ContractTemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    body_md: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)


@router.post("/legal/contract-templates", status_code=status.HTTP_201_CREATED)
async def create_contract_template(
    body: ContractTemplateBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = ContractService(session)
    tid = await svc.create_template(
        tenant_id=tenant_id,
        name=body.name, kind=body.kind, body_md=body.body_md, variables=body.variables,
    )
    await session.commit()
    return {"id": str(tid)}


@router.get("/legal/contract-templates")
async def list_contract_templates(
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    from sqlalchemy import text as _sa_text

    where = "WHERE tenant_id = :tid AND is_active = true"
    params: dict[str, Any] = {"tid": tenant_id}
    if kind:
        where += " AND kind = :kind"
        params["kind"] = kind
    rows = (
        await session.execute(
            _sa_text(
                f"SELECT id, name, kind, variables, created_at "
                f"FROM contract_templates {where} ORDER BY name"
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]


class ContractBody(BaseModel):
    template_id: UUID
    title: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=32)
    variables: dict[str, Any] = Field(default_factory=dict)
    party_id: UUID | None = None
    employee_id: UUID | None = None
    effective_date: date | None = None
    expiration_date: date | None = None


@router.post("/legal/contracts", status_code=status.HTTP_201_CREATED)
async def create_contract(
    body: ContractBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = ContractService(session)
    try:
        cid = await svc.create_contract(
            tenant_id=tenant_id,
            template_id=body.template_id,
            title=body.title,
            kind=body.kind,
            variables=body.variables,
            party_id=body.party_id,
            employee_id=body.employee_id,
            effective_date=str(body.effective_date) if body.effective_date else None,
            expiration_date=str(body.expiration_date) if body.expiration_date else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"id": str(cid)}


class ContractTransitionBody(BaseModel):
    target: str = Field(min_length=1, max_length=32)


@router.post("/legal/contracts/{contract_id}/transition")
async def transition_contract(
    contract_id: UUID,
    body: ContractTransitionBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = ContractService(session)
    try:
        await svc.transition(tenant_id=tenant_id, contract_id=contract_id, target=body.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"id": str(contract_id), "status": body.target}


@router.post("/legal/contracts/{contract_id}/envelopes", status_code=status.HTTP_201_CREATED)
async def create_envelope(
    contract_id: UUID,
    provider: str = Query(default="docusign"),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = ESignService(session)
    eid = await svc.create_envelope(
        tenant_id=tenant_id, contract_id=contract_id, provider=provider
    )
    await session.commit()
    return {"id": str(eid)}


class ESignEventBody(BaseModel):
    event_type: str = Field(min_length=1, max_length=32)
    signer_email: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/legal/envelopes/{envelope_id}/events", status_code=status.HTTP_201_CREATED)
async def record_envelope_event(
    envelope_id: UUID,
    body: ESignEventBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = ESignService(session)
    ev_id = await svc.record_event(
        tenant_id=tenant_id,
        envelope_id=envelope_id,
        event_type=body.event_type,
        signer_email=body.signer_email,
        payload=body.payload,
    )
    await session.commit()
    return {"id": str(ev_id)}
