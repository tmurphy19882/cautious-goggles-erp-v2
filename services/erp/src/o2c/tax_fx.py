"""Tax + FX for O2C and P2P.

W1 ships:
- `compute_line_totals`: per-line gross / net / tax (O2C-12).
- `resolve_tax_rule`: look up the rate by id, or the default for the
  tenant's primary jurisdiction.
- `get_fx_rate`: read the latest `fx_rates` row for a pair (W1 ships
  same-day lookup; W4 adds rate-freshness windows).

Multi-currency conversion is intentionally not in W1 — the SO stores
both `currency` and `fx_rate_id`; consumers that need to convert do
so explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession


_TWO_PLACES = Decimal("0.01")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(slots=True)
class TaxRule:
    id: UUID
    code: str
    rate_pct: Decimal
    is_inclusive: bool


def compute_line_totals(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    discount_pct: Decimal,
    tax_rate_pct: Decimal,
    is_inclusive: bool,
) -> tuple[Decimal, Decimal]:
    """Returns (line_total_incl_tax, tax_amount).

    `line_total_incl_tax` is the gross total the customer pays for
    the line (what the SO/invoice records). `tax_amount` is the
    portion that is tax.
    """
    discount_multiplier = (Decimal("100") - discount_pct) / Decimal("100")
    pre_tax = quantity * unit_price * discount_multiplier
    if is_inclusive:
        # Tax is already inside pre_tax; back it out.
        tax_amount = pre_tax * tax_rate_pct / (Decimal("100") + tax_rate_pct)
        line_total = pre_tax  # gross total stays the same
    else:
        tax_amount = pre_tax * tax_rate_pct / Decimal("100")
        line_total = pre_tax + tax_amount
    return _q(line_total), _q(tax_amount)


async def resolve_tax_rule(
    session: AsyncSession, tenant_id: UUID, tax_rule_id: UUID | None
) -> TaxRule | None:
    if tax_rule_id is None:
        # W1: default to the tenant's first active tax rule.
        row = await session.execute(
            _sa_text(
                "SELECT id, code, rate_pct, is_inclusive FROM tax_rules "
                "WHERE tenant_id = :tenant_id AND is_active = true "
                "ORDER BY code LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
        first = row.mappings().first()
        if first is None:
            return None
        return TaxRule(
            id=first["id"],
            code=first["code"],
            rate_pct=Decimal(str(first["rate_pct"])),
            is_inclusive=bool(first["is_inclusive"]),
        )
    row = await session.execute(
        _sa_text(
            "SELECT id, code, rate_pct, is_inclusive FROM tax_rules "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": tax_rule_id, "tenant_id": tenant_id},
    )
    rule = row.mappings().first()
    if rule is None:
        return None
    return TaxRule(
        id=rule["id"],
        code=rule["code"],
        rate_pct=Decimal(str(rule["rate_pct"])),
        is_inclusive=bool(rule["is_inclusive"]),
    )


async def get_fx_rate(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    base: str,
    quote: str,
    as_of: Any | None = None,
) -> Decimal | None:
    """Return the most recent FX rate for (base, quote)."""
    if base == quote:
        return Decimal("1")
    row = await session.execute(
        _sa_text(
            "SELECT rate FROM fx_rates WHERE tenant_id = :tenant_id "
            "AND base_currency = :base AND quote_currency = :quote "
            "ORDER BY effective_date DESC LIMIT 1"
        ),
        {"tenant_id": tenant_id, "base": base, "quote": quote},
    )
    r = row.scalar_one_or_none()
    return Decimal(str(r)) if r is not None else None
