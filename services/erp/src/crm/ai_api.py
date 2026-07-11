"""W7 — CRM AI + notifications + saved views + custom fields — REST routes.

Routes:
- `GET  /notifications` — list for the current user.
- `POST /notifications/{id}/read` — mark a notification as read.
- `POST /crm/agents/{key}/run` — kick off an AI agent (Sales Coach etc.).
- `GET  /crm/recommendations` — list unread recommendations for the
  current user.
- `GET  /crm/saved-views?entity_type=ticket` — list saved views.
- `POST /crm/saved-views` — create a saved view.
- `POST /crm/custom-fields` — define a custom field.
- `PUT  /crm/custom-fields/{id}/value` — set a value for a custom
  field on a given entity.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, get_tenant_id, get_user_id
from crm.ai import (
    AgentRunner,
    CustomFieldService,
    NotificationService,
    SalesCoachAgent,
    SavedViewService,
)


router = APIRouter(prefix="", tags=["w7-crm-ai"])


# --------------------------------------------------------------------- #
# Notifications                                                           #
# --------------------------------------------------------------------- #


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    svc = NotificationService(session)
    return await svc.list_for_user(
        tenant_id=tenant_id, user_id=user_id, unread_only=unread_only, limit=limit
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: UUID,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> None:
    svc = NotificationService(session)
    await svc.mark_read(tenant_id=tenant_id, notification_id=notification_id)
    await session.commit()


# --------------------------------------------------------------------- #
# AI agents                                                               #
# --------------------------------------------------------------------- #


class AgentRunBody(BaseModel):
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: UUID


@router.post("/crm/agents/{agent_key}/run")
async def run_agent(
    agent_key: str,
    body: AgentRunBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, Any]:
    if agent_key != "sales_coach":
        raise HTTPException(status_code=404, detail="unknown agent")
    runner = AgentRunner(session)
    rid = await runner.run_agent(
        agent_key=agent_key,
        agent=SalesCoachAgent(),
        tenant_id=tenant_id,
        user_id=user_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
    )
    await session.commit()
    return {"run_id": str(rid)}


@router.get("/crm/recommendations")
async def list_recommendations(
    subject_type: str | None = Query(default=None),
    subject_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    from sqlalchemy import text as _sa_text

    where = "WHERE r.tenant_id = :tid AND r.dismissed_at IS NULL AND r.acted_at IS NULL"
    params: dict[str, Any] = {"tid": tenant_id, "limit": limit}
    join = "FROM ai_recommendations r JOIN ai_agent_runs a ON a.id = r.run_id"
    if subject_type:
        where += " AND a.subject_type = :stype"
        params["stype"] = subject_type
    if subject_id:
        where += " AND a.subject_id = :sid"
        params["sid"] = subject_id
    rows = (
        await session.execute(
            _sa_text(
                f"SELECT r.id, r.kind, r.title, r.body, r.confidence, r.action_url, "
                f"r.created_at, a.subject_type, a.subject_id, a.agent_key "
                f"{join} {where} ORDER BY r.created_at DESC LIMIT :limit"
            ),
            params,
        )
    ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------- #
# Saved views                                                             #
# --------------------------------------------------------------------- #


class SavedViewBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    entity_type: str = Field(min_length=1, max_length=64)
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: list[dict[str, Any]] = Field(default_factory=list)
    is_shared: bool = False


@router.post("/crm/saved-views", status_code=status.HTTP_201_CREATED)
async def create_saved_view(
    body: SavedViewBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = SavedViewService(session)
    vid = await svc.create(
        tenant_id=tenant_id,
        user_id=user_id,
        name=body.name,
        entity_type=body.entity_type,
        filters=body.filters,
        sort=body.sort,
        is_shared=body.is_shared,
    )
    await session.commit()
    return {"id": str(vid)}


@router.get("/crm/saved-views")
async def list_saved_views(
    entity_type: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> list[dict[str, Any]]:
    svc = SavedViewService(session)
    return await svc.list_for_user(tenant_id=tenant_id, user_id=user_id, entity_type=entity_type)


# --------------------------------------------------------------------- #
# Custom fields                                                           #
# --------------------------------------------------------------------- #


class CustomFieldBody(BaseModel):
    entity_type: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    field_type: str = Field(min_length=1, max_length=32)
    options: dict[str, Any] | None = None
    is_required: bool = False


class CustomFieldValueBody(BaseModel):
    entity_id: UUID
    value_text: str | None = None
    value_number: float | None = None
    value_date: str | None = None
    value_bool: bool | None = None


@router.post("/crm/custom-fields", status_code=status.HTTP_201_CREATED)
async def define_custom_field(
    body: CustomFieldBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> dict[str, str]:
    svc = CustomFieldService(session)
    try:
        fid = await svc.define(
            tenant_id=tenant_id,
            entity_type=body.entity_type,
            key=body.key,
            label=body.label,
            field_type=body.field_type,
            options=body.options,
            is_required=body.is_required,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {"id": str(fid)}


@router.put("/crm/custom-fields/{field_id}/value", status_code=status.HTTP_204_NO_CONTENT)
async def set_custom_field_value(
    field_id: UUID,
    body: CustomFieldValueBody,
    session: AsyncSession = Depends(get_session),
    tenant_id: UUID = Depends(get_tenant_id),
    user_id: UUID = Depends(get_user_id),
) -> None:
    from decimal import Decimal
    svc = CustomFieldService(session)
    await svc.set_value(
        tenant_id=tenant_id,
        field_id=field_id,
        entity_id=body.entity_id,
        value_text=body.value_text,
        value_number=Decimal(str(body.value_number)) if body.value_number is not None else None,
        value_date=body.value_date,
        value_bool=body.value_bool,
    )
    await session.commit()
