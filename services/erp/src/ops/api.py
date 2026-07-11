"""W10 — Operational readiness REST routes.

- `GET  /ops/dlq` — list unresolved DLQ entries.
- `POST /ops/dlq/{id}/resolve` — mark a DLQ entry resolved.
- `POST /ops/contracts/{event_type}` — register an event schema.
- `GET  /ops/contracts/{event_type}` — fetch the current event schema.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, get_tenant_id, get_user_id
from ops.service import DLQService, EventSchemaRegistry


router = APIRouter(prefix="", tags=["w10-ops"])


# --------------------------------------------------------------------- #
# DLQ                                                                     #
# --------------------------------------------------------------------- #


@router.get("/ops/dlq")
async def list_dlq(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    svc = DLQService(session)
    return await svc.list_unresolved(tenant_id=tenant_id, limit=limit)


class DLQResolveBody(BaseModel):
    note: str = Field(min_length=1)


@router.post("/ops/dlq/{dlq_id}/resolve", status_code=status.HTTP_204_NO_CONTENT)
async def resolve_dlq(
    dlq_id: UUID,
    body: DLQResolveBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> None:
    svc = DLQService(session)
    await svc.resolve(tenant_id=tenant_id, dlq_id=dlq_id, note=body.note)
    await session.commit()


# --------------------------------------------------------------------- #
# Event schema registry                                                   #
# --------------------------------------------------------------------- #


class EventSchemaBody(BaseModel):
    version: int = Field(ge=1)
    schema: dict[str, Any]


@router.post("/ops/contracts/{event_type}", status_code=status.HTTP_201_CREATED)
async def register_event_schema(
    event_type: str,
    body: EventSchemaBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, Any]:
    svc = EventSchemaRegistry(session)
    await svc.register(event_type=event_type, version=body.version, schema=body.schema)
    await session.commit()
    return {"event_type": event_type, "version": body.version}


@router.get("/ops/contracts/{event_type}")
async def get_event_schema(
    event_type: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, Any]:
    svc = EventSchemaRegistry(session)
    rec = await svc.current(event_type=event_type)
    if rec is None:
        raise HTTPException(status_code=404, detail="event type not registered")
    return rec
