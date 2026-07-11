"""FastAPI router for master-data endpoints (W1).

Endpoints (all under `/api/v1/erp/master-data`):

  POST   /products                  — create
  GET    /products                  — list
  GET    /products/{id}             — fetch
  POST   /products/{id}/layers      — add an inventory valuation layer (FIFO)

  POST   /locations                 — create
  GET    /locations                 — list
  GET    /locations/{id}            — fetch

  POST   /customers                 — create a customer (a User with is_service_account=false)
  GET    /customers                 — list customers
  GET    /customers/{id}            — fetch

  POST   /credit-limits             — set a credit limit for a customer
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

from identity.deps import require_permission
from shared.tenant import require_tenant_id

router = APIRouter(prefix="/master-data", tags=["master-data"])


# ---------- products ----------


class CreateProductBody(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    uom: str = "each"
    is_active: bool = True


@router.post(
    "/products",
    status_code=201,
    summary="Create a product",
    dependencies=[Depends(require_permission("master.product.write"))],
)
async def create_product(
    body: CreateProductBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    pid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO products (id, tenant_id, sku, name, description, uom, is_active)
                VALUES (:id, :tenant_id, :sku, :name, :description, :uom, :is_active)
                """
            ),
            {
                "id": pid,
                "tenant_id": tenant_id,
                "sku": body.sku,
                "name": body.name,
                "description": body.description,
                "uom": body.uom,
                "is_active": body.is_active,
            },
        )
        await session.commit()
    return {"id": str(pid), "sku": body.sku, "name": body.name}


@router.get(
    "/products",
    summary="List products",
    dependencies=[Depends(require_permission("master.product.read"))],
)
async def list_products(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
    limit: int = 100,
) -> dict[str, Any]:
    limit = max(1, min(500, limit))
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT id, sku, name, uom, is_active FROM products "
                    "WHERE tenant_id = :tenant_id AND deleted_at IS NULL "
                    "ORDER BY name LIMIT :limit"
                ),
                {"tenant_id": tenant_id, "limit": limit},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.get(
    "/products/{product_id}",
    summary="Fetch a product",
    dependencies=[Depends(require_permission("master.product.read"))],
)
async def get_product(
    product_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        row = (
            await session.execute(
                _sa_text(
                    "SELECT * FROM products WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": product_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "product not found"})
    return dict(row)


class AddInventoryLayerBody(BaseModel):
    location_id: UUID
    quantity: str = Field(description="Decimal as string")
    unit_cost: str = Field(description="Decimal as string")
    source: str = "purchase"  # purchase | adjustment | opening
    source_ref: str | None = None


@router.post(
    "/products/{product_id}/layers",
    status_code=201,
    summary="Add an inventory valuation layer (FIFO)",
    dependencies=[Depends(require_permission("master.product.write"))],
)
async def add_inventory_layer(
    product_id: UUID,
    body: AddInventoryLayerBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    layer_id = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO inventory_valuation (
                    id, tenant_id, product_id, location_id, layer_date,
                    quantity, unit_cost, source, source_ref
                ) VALUES (
                    :id, :tenant_id, :product_id, :location_id, now(),
                    :quantity, :unit_cost, :source, :source_ref
                )
                """
            ),
            {
                "id": layer_id,
                "tenant_id": tenant_id,
                "product_id": product_id,
                "location_id": body.location_id,
                "quantity": Decimal(body.quantity),
                "unit_cost": Decimal(body.unit_cost),
                "source": body.source,
                "source_ref": body.source_ref,
            },
        )
        await session.commit()
    return {"id": str(layer_id), "product_id": str(product_id)}


# ---------- locations ----------


class CreateLocationBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=32)  # warehouse | plant | address | ftz
    address_line1: str | None = None
    city: str | None = None
    region: str | None = None
    country_code: str | None = None
    is_active: bool = True


@router.post(
    "/locations",
    status_code=201,
    summary="Create a location",
    dependencies=[Depends(require_permission("master.location.write"))],
)
async def create_location(
    body: CreateLocationBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    lid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO locations (
                    id, tenant_id, code, name, kind,
                    address_line1, city, region, country_code, is_active
                ) VALUES (
                    :id, :tenant_id, :code, :name, :kind,
                    :address_line1, :city, :region, :country_code, :is_active
                )
                """
            ),
            {
                "id": lid,
                "tenant_id": tenant_id,
                "code": body.code,
                "name": body.name,
                "kind": body.kind,
                "address_line1": body.address_line1,
                "city": body.city,
                "region": body.region,
                "country_code": body.country_code,
                "is_active": body.is_active,
            },
        )
        await session.commit()
    return {"id": str(lid), "code": body.code, "name": body.name, "kind": body.kind}


@router.get(
    "/locations",
    summary="List locations",
    dependencies=[Depends(require_permission("master.location.read"))],
)
async def list_locations(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT id, code, name, kind, country_code, is_active "
                    "FROM locations WHERE tenant_id = :tenant_id AND deleted_at IS NULL "
                    "ORDER BY name"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


# ---------- customers (thin — full party module in W3) ----------


class CreateCustomerBody(BaseModel):
    email: str
    display_name: str | None = None
    is_active: bool = True


@router.post(
    "/customers",
    status_code=201,
    summary="Create a customer (a User with is_service_account=false)",
    dependencies=[Depends(require_permission("master.party.write"))],
)
async def create_customer(
    body: CreateCustomerBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    uid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO users (
                    id, tenant_id, email, display_name, is_active, is_service_account
                ) VALUES (
                    :id, :tenant_id, :email, :display_name, :is_active, false
                )
                """
            ),
            {
                "id": uid,
                "tenant_id": tenant_id,
                "email": body.email,
                "display_name": body.display_name,
                "is_active": body.is_active,
            },
        )
        await session.commit()
    return {"id": str(uid), "email": body.email}


@router.get(
    "/customers",
    summary="List customers",
    dependencies=[Depends(require_permission("master.party.read"))],
)
async def list_customers(
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with sf() as session:
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT id, email, display_name, is_active "
                    "FROM users WHERE tenant_id = :tenant_id AND is_service_account = false "
                    "AND deleted_at IS NULL ORDER BY email"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


# ---------- credit limits ----------


class CreateCreditLimitBody(BaseModel):
    customer_id: UUID
    currency: str = "USD"
    limit_amount: str = Field(description="Decimal as string")
    hold_reason: str | None = None


@router.post(
    "/credit-limits",
    status_code=201,
    summary="Set a credit limit for a customer",
    dependencies=[Depends(require_permission("o2c.credit.write"))],
)
async def create_credit_limit(
    body: CreateCreditLimitBody,
    request: Request,
    tenant_id: UUID = Depends(require_tenant_id),
) -> dict[str, Any]:
    sf: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    clid = uuid4()
    async with sf() as session:
        await session.execute(
            _sa_text(
                """
                INSERT INTO credit_limits (
                    id, tenant_id, customer_id, currency, limit_amount, exposure_amount
                ) VALUES (
                    :id, :tenant_id, :customer_id, :currency, :limit_amount, 0
                )
                """
            ),
            {
                "id": clid,
                "tenant_id": tenant_id,
                "customer_id": body.customer_id,
                "currency": body.currency,
                "limit_amount": Decimal(body.limit_amount),
            },
        )
        await session.commit()
    return {"id": str(clid), "customer_id": str(body.customer_id), "currency": body.currency}
