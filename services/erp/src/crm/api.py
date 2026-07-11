"""CRM core REST API (W5).

Endpoints under `/api/v1/erp/crm`:

  POST   /pipelines                          — create
  POST   /pipelines/{id}/stages              — add a stage

  POST   /leads                              — create
  POST   /leads/{id}/qualify                 — qualify
  POST   /leads/{id}/convert                 — lead → party + opportunity

  POST   /opportunities                      — create
  POST   /opportunities/{id}/stage           — transition stage
  POST   /opportunities/{id}/won
  POST   /opportunities/{id}/lost

  POST   /quotes                             — create
  POST   /quotes/{id}/send
  POST   /quotes/{id}/accept
  POST   /quotes/{id}/convert-to-so          — O2C-15

  POST   /activities                         — log activity
  GET    /activities                         — list for a subject

  POST   /tickets                            — create
  POST   /tickets/{id}/comments              — comment
  POST   /tickets/{id}/status                — transition
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crm.service import (
    CreateLeadInput,
    CreateQuoteInput,
    CreateQuoteLineInput,
    LeadService,
    QuoteService,
)
from identity.deps import require_permission
from shared.tenant import require_tenant_id

router = APIRouter(prefix="/crm", tags=["crm"])


# ---------- pipelines + stages ----------


class CreatePipelineBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    is_default: bool = False


class CreateStageBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    position: int
    probability_pct: str = "0"
    is_won: bool = False
    is_lost: bool = False


@router.post(
    "/pipelines",
    status_code=201,
    summary="Create a pipeline",
    dependencies=[Depends(require_permission("crm.opp.write"))],
)
async def create_pipeline(
    body: CreatePipelineBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    pid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO pipelines (id, tenant_id, name, description, is_default)
                VALUES (:id, :tenant_id, :name, :desc, :is_default)
                """
            ),
            {"id": pid, "tenant_id": tenant_id, "name": body.name, "desc": body.description, "is_default": body.is_default},
        )
        await session.commit()
    return {"id": str(pid), "name": body.name}


@router.post(
    "/pipelines/{pipeline_id}/stages",
    status_code=201,
    summary="Add a stage to a pipeline",
    dependencies=[Depends(require_permission("crm.opp.write"))],
)
async def add_stage(
    pipeline_id: UUID,
    body: CreateStageBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    sid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO pipeline_stages (
                    id, tenant_id, pipeline_id, key, name, position, probability_pct, is_won, is_lost
                ) VALUES (
                    :id, :tenant_id, :pid, :key, :name, :pos, :prob, :won, :lost
                )
                """
            ),
            {
                "id": sid, "tenant_id": tenant_id, "pid": pipeline_id, "key": body.key,
                "name": body.name, "pos": body.position, "prob": Decimal(body.probability_pct),
                "won": body.is_won, "lost": body.is_lost,
            },
        )
        await session.commit()
    return {"id": str(sid), "key": body.key}


# ---------- leads ----------


class CreateLeadBody(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str = "manual"
    notes: str | None = None


@router.post(
    "/leads",
    status_code=201,
    summary="Create a lead",
    dependencies=[Depends(require_permission("crm.lead.write"))],
)
async def create_lead(
    body: CreateLeadBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = LeadService(session)
        r = await svc.create(
            tenant_id=tenant_id,
            input=CreateLeadInput(
                company_name=body.company_name,
                contact_name=body.contact_name,
                email=body.email,
                phone=body.phone,
                source=body.source,
                notes=body.notes,
            ),
        )
        await session.commit()
    return {"id": str(r.lead_id), "status": r.status}


class QualifyLeadBody(BaseModel):
    score: int = 50


@router.post(
    "/leads/{lead_id}/qualify",
    summary="Qualify a lead",
    dependencies=[Depends(require_permission("crm.lead.write"))],
)
async def qualify_lead(
    lead_id: UUID,
    body: QualifyLeadBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = LeadService(session)
        r = await svc.qualify(tenant_id=tenant_id, lead_id=lead_id, score=body.score)
        await session.commit()
    return r


class ConvertLeadBody(BaseModel):
    party_code: str
    opportunity_name: str
    pipeline_id: UUID
    stage_id: UUID
    amount: str = "0"


@router.post(
    "/leads/{lead_id}/convert",
    summary="Convert a lead to a Party + Opportunity",
    dependencies=[Depends(require_permission("crm.lead.write"))],
)
async def convert_lead(
    lead_id: UUID,
    body: ConvertLeadBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with sf() as session:
            svc = LeadService(session)
            r = await svc.convert(
                tenant_id=tenant_id,
                lead_id=lead_id,
                party_code=body.party_code,
                opportunity_name=body.opportunity_name,
                pipeline_id=body.pipeline_id,
                stage_id=body.stage_id,
                amount=Decimal(body.amount),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return r


# ---------- quotes ----------


class CreateQuoteLineBody(BaseModel):
    product_id: UUID
    sku: str
    description: str | None = None
    quantity: str
    unit: str = "each"
    unit_price: str
    discount_pct: str = "0"
    tax_rule_id: UUID | None = None


class CreateQuoteBody(BaseModel):
    party_id: UUID
    opportunity_id: UUID | None = None
    currency: str = "USD"
    valid_until: Any | None = None
    notes: str | None = None
    lines: list[CreateQuoteLineBody] = Field(min_length=1)


@router.post(
    "/quotes",
    status_code=201,
    summary="Create a quote",
    dependencies=[Depends(require_permission("crm.quote.write"))],
)
async def create_quote(
    body: CreateQuoteBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lines = [
        CreateQuoteLineInput(
            product_id=l.product_id, sku=l.sku, description=l.description,
            quantity=Decimal(l.quantity), unit=l.unit, unit_price=Decimal(l.unit_price),
            discount_pct=Decimal(l.discount_pct), tax_rule_id=l.tax_rule_id,
        )
        for l in body.lines
    ]
    try:
        async with sf() as session:
            svc = QuoteService(session)
            r = await svc.create(
                tenant_id=tenant_id,
                input=CreateQuoteInput(
                    party_id=body.party_id,
                    opportunity_id=body.opportunity_id,
                    currency=body.currency,
                    valid_until=body.valid_until,
                    notes=body.notes,
                    lines=lines,
                ),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "id": str(r.quote_id), "quote_number": r.quote_number,
        "total_amount": str(r.total_amount), "line_count": r.line_count,
    }


@router.post(
    "/quotes/{quote_id}/send",
    summary="Mark a quote as sent",
    dependencies=[Depends(require_permission("crm.quote.write"))],
)
async def send_quote(
    quote_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = QuoteService(session)
        await svc.send(tenant_id=tenant_id, quote_id=quote_id)
        await session.commit()
    return {"id": str(quote_id), "status": "sent"}


@router.post(
    "/quotes/{quote_id}/accept",
    summary="Mark a quote as accepted (gates convert-to-SO)",
    dependencies=[Depends(require_permission("crm.quote.write"))],
)
async def accept_quote(
    quote_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = QuoteService(session)
        await svc.accept(tenant_id=tenant_id, quote_id=quote_id)
        await session.commit()
    return {"id": str(quote_id), "status": "accepted"}


@router.post(
    "/quotes/{quote_id}/convert-to-so",
    summary="Convert an accepted quote to a sales order (O2C-15)",
    dependencies=[Depends(require_permission("crm.quote.write"))],
)
async def convert_quote_to_so(
    quote_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with sf() as session:
            svc = QuoteService(session)
            r = await svc.convert_to_sales_order(tenant_id=tenant_id, quote_id=quote_id)
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return r
