"""W9 — Trade REST routes.

- `POST /trade/hts/resolve` — auto-resolve HTS from product description.
- `POST/GET /trade/ftz/entries` — admit + list FTZ entries.
- `POST /trade/ftz/entries/{id}/remove` — mark an entry as removed.
- `GET /trade/ftz/weekly-inventory` — weekly inventory report.
- `POST /trade/screen` — re-screen a party against OFAC SDN (stub).
- `GET /trade/screen/{party_id}/latest` — latest screening snapshot.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, get_tenant_id, get_user_id
from trade.service import FTZService, HTSResolver, ScreeningService


router = APIRouter(prefix="", tags=["w9-trade"])


# --------------------------------------------------------------------- #
# HTS                                                                     #
# --------------------------------------------------------------------- #


class HTSResolveBody(BaseModel):
    description: str = Field(min_length=1)


@router.post("/trade/hts/resolve")
async def resolve_hts(
    body: HTSResolveBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    resolver = HTSResolver(session)
    matches = await resolver.resolve(tenant_id=tenant_id, description=body.description)
    return [
        {"code": m.code, "description": m.description, "score": m.score}
        for m in matches
    ]


# --------------------------------------------------------------------- #
# FTZ                                                                     #
# --------------------------------------------------------------------- #


class FTZAdmitBody(BaseModel):
    entry_number: str = Field(min_length=1, max_length=32)
    zone_id: str = Field(min_length=1, max_length=16)
    admission_date: date
    hts_code: str | None = None
    quantity: Decimal
    value: Decimal
    unit: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    party_id: UUID | None = None


@router.post("/trade/ftz/entries", status_code=status.HTTP_201_CREATED)
async def admit_ftz(
    body: FTZAdmitBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = FTZService(session)
    fid = await svc.admit(
        tenant_id=tenant_id,
        entry_number=body.entry_number,
        zone_id=body.zone_id,
        admission_date=str(body.admission_date),
        hts_code=body.hts_code,
        quantity=body.quantity,
        value=body.value,
        unit=body.unit,
        currency=body.currency,
        party_id=body.party_id,
    )
    await session.commit()
    return {"id": str(fid)}


@router.post("/trade/ftz/entries/{entry_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_ftz(
    entry_id: UUID,
    removal_date: date,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> None:
    svc = FTZService(session)
    await svc.remove(
        tenant_id=tenant_id, entry_id=entry_id, removal_date=str(removal_date)
    )
    await session.commit()


@router.get("/trade/ftz/weekly-inventory")
async def weekly_inventory(
    as_of: date,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    svc = FTZService(session)
    return await svc.weekly_inventory(tenant_id=tenant_id, as_of=str(as_of))


# --------------------------------------------------------------------- #
# Screening                                                               #
# --------------------------------------------------------------------- #


class ScreenBody(BaseModel):
    party_id: UUID
    party_name: str = Field(min_length=1, max_length=255)
    source: str = Field(default="ofac_sdn", min_length=1, max_length=32)


@router.post("/trade/screen", status_code=status.HTTP_201_CREATED)
async def screen_party(
    body: ScreenBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, Any]:
    svc = ScreeningService(session)
    sid = await svc.screen(
        tenant_id=tenant_id,
        party_id=body.party_id,
        party_name=body.party_name,
        source=body.source,
    )
    await session.commit()
    return {"id": str(sid)}


@router.get("/trade/screen/{party_id}/latest")
async def latest_screening(
    party_id: UUID,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, Any]:
    svc = ScreeningService(session)
    snap = await svc.latest(tenant_id=tenant_id, party_id=party_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="no screening snapshot")
    return snap
