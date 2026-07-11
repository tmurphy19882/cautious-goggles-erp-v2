"""Credit check + credit hold/release for O2C (O2C-2).

W1 ships a simple per-currency credit limit model:
- `credit_limits.limit_amount` — the max exposure allowed for the
  customer in the given currency.
- `credit_limits.exposure_amount` — the current open exposure (sum of
  draft + confirmed + invoiced - paid orders).
- `credit_limits.hold_reason` / `held_at` / `held_by` — when the
  limit is exceeded, the order is placed on `credit_hold`.

A real implementation would do a per-currency FX conversion before
comparing. W1 ships same-currency comparison; W4 (Finance) extends
to multi-currency.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow


@dataclass(slots=True)
class CreditCheckResult:
    status: str  # "ok" | "hold"
    new_exposure: Decimal
    limit_amount: Decimal
    event_id: UUID


async def check_credit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    customer_id: UUID,
    currency: str,
    amount: Decimal,
) -> tuple[str, UUID]:
    """Check credit for `(customer, currency, amount)`.

    Returns `(new_status, event_id)`. `new_status` is "ok" if the
    order can proceed, "credit_hold" if the limit is exceeded.
    `event_id` is a fresh UUID for the decision (caller writes it to
    an audit row or outbox event).
    """
    # Look up the limit (same-currency only in W1).
    row = await session.execute(
        _sa_text(
            "SELECT id, limit_amount, exposure_amount FROM credit_limits "
            "WHERE tenant_id = :tenant_id AND customer_id = :customer_id AND currency = :currency"
        ),
        {"tenant_id": tenant_id, "customer_id": customer_id, "currency": currency},
    )
    limit = row.mappings().first()
    if limit is None:
        # No limit record → default to unlimited. W6's onboarding flow
        # will seed one per customer.
        return "ok", uuid4()

    new_exposure = Decimal(str(limit["exposure_amount"])) + amount
    if new_exposure > Decimal(str(limit["limit_amount"])):
        # Place the credit_limits row on hold and return.
        await session.execute(
            _sa_text(
                "UPDATE credit_limits SET hold_reason = :reason, held_at = :held_at, "
                "exposure_amount = :exposure WHERE id = :id"
            ),
            {
                "reason": f"new exposure {new_exposure} > limit {limit['limit_amount']}",
                "held_at": utcnow(),
                "exposure": new_exposure,
                "id": limit["id"],
            },
        )
        return "credit_hold", uuid4()

    # OK — bump exposure and return.
    await session.execute(
        _sa_text("UPDATE credit_limits SET exposure_amount = exposure_amount + :amount WHERE id = :id"),
        {"amount": amount, "id": limit["id"]},
    )
    return "ok", uuid4()


async def release_credit_hold_if_held(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    customer_id: UUID,
    amount: Decimal,
) -> None:
    """Release the held exposure (called on SO cancel)."""
    await session.execute(
        _sa_text(
            "UPDATE credit_limits SET exposure_amount = GREATEST(0, exposure_amount - :amount), "
            "hold_reason = NULL, held_at = NULL "
            "WHERE tenant_id = :tenant_id AND customer_id = :customer_id"
        ),
        {"amount": amount, "tenant_id": tenant_id, "customer_id": customer_id},
    )
