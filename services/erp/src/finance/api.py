"""Finance / GL REST API (W4).

Endpoints under `/api/v1/erp/finance`:

  POST   /gl/accounts               — create a GL account
  GET    /gl/accounts               — list accounts (optionally by kind)

  POST   /periods                   — open an accounting period
  POST   /periods/{id}/close        — close a period (FIN-4)

  POST   /journal-entries           — post a manual journal (FIN-2)
  GET    /journal-entries/{id}      — fetch
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finance.gl import (
    JournalLineInput,
    PostJournalInput,
    GLService,
    KIND_ASSET,
    KIND_LIABILITY,
    KIND_EQUITY,
    KIND_REVENUE,
    KIND_EXPENSE,
    SIDE_DEBIT,
    SIDE_CREDIT,
)
from identity.deps import require_permission
from shared.tenant import require_tenant_id

router = APIRouter(prefix="/finance", tags=["finance"])


# ---------- gl accounts ----------


class CreateGLAccountBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(pattern="^(asset|liability|equity|revenue|expense)$")
    normal_side: str = Field(pattern="^(debit|credit)$")
    parent_id: UUID | None = None
    description: str | None = None


@router.post(
    "/gl/accounts",
    status_code=201,
    summary="Create a GL account",
    dependencies=[Depends(require_permission("finance.coa.write"))],
)
async def create_gl_account(
    body: CreateGLAccountBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = GLService(session)
        aid = await svc.create_account(tenant_id=tenant_id, **body.model_dump())
        await session.commit()
    return {"id": str(aid), "code": body.code, "kind": body.kind}


@router.get(
    "/gl/accounts",
    summary="List GL accounts",
    dependencies=[Depends(require_permission("finance.coa.read"))],
)
async def list_gl_accounts(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
    kind: str | None = None,
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        params: dict[str, Any] = {"tenant_id": tenant_id}
        where = "WHERE tenant_id = :tenant_id AND deleted_at IS NULL"
        if kind:
            where += " AND kind = :kind"
            params["kind"] = kind
        rows = (
            await session.execute(
                _sa_text(
                    f"SELECT id, code, name, kind, normal_side, is_active FROM gl_accounts {where} ORDER BY code"
                ),
                params,
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


# ---------- periods ----------


class OpenPeriodBody(BaseModel):
    code: str = Field(min_length=1, max_length=16, pattern=r"^\d{4}-\d{2}$")
    start_date: date
    end_date: date


@router.post(
    "/periods",
    status_code=201,
    summary="Open an accounting period (FIN-4)",
    dependencies=[Depends(require_permission("finance.period.close"))],
)
async def open_period(
    body: OpenPeriodBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        svc = GLService(session)
        pid = await svc.open_period(
            tenant_id=tenant_id, **body.model_dump()
        )
        await session.commit()
    return {"id": str(pid), "code": body.code}


@router.post(
    "/periods/{period_id}/close",
    summary="Close a period (FIN-4)",
    dependencies=[Depends(require_permission("finance.period.close"))],
)
async def close_period(
    period_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
    closed_by: UUID | None = None,
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    if closed_by is None:
        raise HTTPException(400, detail={"code": "missing_approver", "message": "closed_by is required"})
    async with sf() as session:
        svc = GLService(session)
        r = await svc.close_period(
            tenant_id=tenant_id, period_id=period_id, closed_by=closed_by
        )
        await session.commit()
    return r


# ---------- journal entries ----------


class JournalLineBody(BaseModel):
    account_id: UUID
    description: str | None = None
    debit_amount: str = "0"
    credit_amount: str = "0"
    party_id: UUID | None = None
    ar_invoice_id: UUID | None = None
    ap_invoice_id: UUID | None = None


class PostJournalBody(BaseModel):
    entry_date: date
    source: str = Field(min_length=1, max_length=32)
    source_id: UUID | None = None
    description: str | None = None
    period_id: UUID | None = None
    lines: list[JournalLineBody] = Field(min_length=2)


@router.post(
    "/journal-entries",
    status_code=201,
    summary="Post a manual journal entry (FIN-2)",
    dependencies=[Depends(require_permission("finance.journal.write"))],
)
async def post_journal(
    body: PostJournalBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lines = [
        JournalLineInput(
            account_id=ln.account_id,
            description=ln.description,
            debit_amount=Decimal(ln.debit_amount),
            credit_amount=Decimal(ln.credit_amount),
            party_id=ln.party_id,
            ar_invoice_id=ln.ar_invoice_id,
            ap_invoice_id=ln.ap_invoice_id,
        )
        for ln in body.lines
    ]
    try:
        async with sf() as session:
            svc = GLService(session)
            r = await svc.post_journal(
                tenant_id=tenant_id,
                input=PostJournalInput(
                    entry_date=body.entry_date,
                    source=body.source,
                    source_id=body.source_id,
                    description=body.description,
                    period_id=body.period_id,
                    lines=lines,
                ),
            )
            await session.commit()
    except ValueError as exc:
        raise HTTPException(400, detail={"code": "invalid", "message": str(exc)})
    return {
        "entry_id": str(r.entry_id),
        "entry_number": r.entry_number,
        "total_debit": str(r.total_debit),
        "total_credit": str(r.total_credit),
    }


@router.get(
    "/journal-entries/{entry_id}",
    summary="Fetch a journal entry",
    dependencies=[Depends(require_permission("finance.journal.read"))],
)
async def get_journal_entry(
    entry_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        entry = (
            await session.execute(
                _sa_text(
                    "SELECT * FROM journal_entries WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": entry_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if entry is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "entry not found"})
        lines = (
            await session.execute(
                _sa_text(
                    "SELECT * FROM journal_lines WHERE entry_id = :id "
                    "AND tenant_id = :tenant_id ORDER BY line_number"
                ),
                {"id": entry_id, "tenant_id": tenant_id},
            )
        ).mappings().all()
    return {
        "id": str(entry["id"]),
        "entry_number": entry["entry_number"],
        "entry_date": entry["entry_date"].isoformat() if entry["entry_date"] else None,
        "source": entry["source"],
        "description": entry["description"],
        "total_debit": str(entry["total_debit"]),
        "total_credit": str(entry["total_credit"]),
        "lines": [dict(l) for l in lines],
    }
