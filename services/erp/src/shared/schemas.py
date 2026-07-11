"""Pydantic v2 base models for ERP v2.

Conventions:

- All money uses `Decimal` (never float), serialised as `str` to keep precision.
- All time uses `datetime` with `tzinfo=UTC`.
- All IDs are `UUID`.
- All IDs get a `pydantic.alias_generator` for snake/camel interop with the
  frontend, controlled by the model config (default: snake).
- `TenantBoundModel` enforces that `tenant_id` is present and equal to the
  current tenant (callers should set it from the request context, not the
  request body).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# A non-empty trimmed string of reasonable length.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
ShortStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
CodeStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]*$")]
CurrencyCode = Annotated[str, StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


def utcnow() -> datetime:
    """Timezone-aware UTC now. Use everywhere instead of `datetime.utcnow()`."""
    return datetime.now(tz=timezone.utc)


class AppModel(BaseModel):
    """Base for all ERP v2 response/request models.

    - `from_attributes=True` so Pydantic v2 can build models from SQLAlchemy rows.
    - `populate_by_name=True` so both alias and field name work.
    - `extra="forbid"` so unknown fields fail loudly.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class TenantBoundModel(AppModel):
    """Marker for any model that must be tenant-scoped.

    The actual `tenant_id` is set by the service layer from the request
    context, never trusted from the request body.
    """

    tenant_id: UUID = Field(description="Owning tenant; set by service from request context")


class MoneyDecimal(BaseModel):
    """Money as decimal + currency. Use this on every monetary value."""

    value: Decimal = Field(..., max_digits=20, decimal_places=4, description="Decimal money value, never float")
    currency: CurrencyCode = Field(default="USD", description="ISO 4217 currency code")

    model_config = ConfigDict(from_attributes=True)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.value} {self.currency}"


class PageRequest(AppModel):
    """Standard pagination input."""

    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=50, ge=1, le=500)


class PageResponse[T](AppModel):
    """Standard pagination output. Generic over the item model."""

    items: list[T]
    page: int
    page_size: int
    total: int


def to_dict(model: BaseModel, **overrides: Any) -> dict[str, Any]:
    """Dump a model to a plain dict, with optional overrides for nested writes."""
    payload = model.model_dump(mode="json", exclude_unset=True)
    payload.update(overrides)
    return payload
