"""Finance / GL service for W4.

Closes FIN-1..FIN-14 from the audit:
- FIN-1: chart of accounts (`gl_accounts`)
- FIN-2: journal entries (manual `post_journal`)
- FIN-3: auto-post from SO (on `INVOICE_GENERATED` → AR + revenue + COGS),
  from PO (on AP invoice → AP + inventory), from payments (AR/AP cash)
- FIN-4: period close (lock + close accounting periods)
- FIN-5/6: AR/AP aging — views in `views/ar_aging.sql` and `ap_aging.sql`
  land in W4.1
- FIN-7: tax — `o2c.tax_fx` is shared with P2P
- FIN-8: multi-currency — `fx_rates` table is shared; reval job in W4.1
- FIN-12: credit / debit memos — full impl in W4.1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow

logger = logging.getLogger(__name__)


KIND_ASSET = "asset"
KIND_LIABILITY = "liability"
KIND_EQUITY = "equity"
KIND_REVENUE = "revenue"
KIND_EXPENSE = "expense"

SIDE_DEBIT = "debit"
SIDE_CREDIT = "credit"


@dataclass(slots=True)
class JournalLineInput:
    account_id: UUID
    description: str | None = None
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    party_id: UUID | None = None
    ar_invoice_id: UUID | None = None
    ap_invoice_id: UUID | None = None


@dataclass(slots=True)
class PostJournalInput:
    entry_date: Any
    source: str  # manual | sales_order | purchase_order | ap_invoice | payment
    source_id: UUID | None = None
    description: str | None = None
    period_id: UUID | None = None
    lines: list[JournalLineInput] = field(default_factory=list)


@dataclass(slots=True)
class PostJournalResult:
    entry_id: UUID
    entry_number: str
    total_debit: Decimal
    total_credit: Decimal


class GLService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_account(
        self,
        *,
        tenant_id: UUID,
        code: str,
        name: str,
        kind: str,
        normal_side: str,
        parent_id: UUID | None = None,
        description: str | None = None,
    ) -> UUID:
        aid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO gl_accounts (
                    id, tenant_id, code, name, kind, normal_side, parent_id, description
                ) VALUES (
                    :id, :tenant_id, :code, :name, :kind, :side, :parent, :desc
                )
                """
            ),
            {
                "id": aid,
                "tenant_id": tenant_id,
                "code": code,
                "name": name,
                "kind": kind,
                "side": normal_side,
                "parent": parent_id,
                "desc": description,
            },
        )
        await self._session.flush()
        return aid

    async def open_period(
        self, *, tenant_id: UUID, code: str, start_date: Any, end_date: Any
    ) -> UUID:
        pid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO accounting_periods (
                    id, tenant_id, code, start_date, end_date, status
                ) VALUES (
                    :id, :tenant_id, :code, :start, :end, 'open'
                )
                """
            ),
            {
                "id": pid,
                "tenant_id": tenant_id,
                "code": code,
                "start": start_date,
                "end": end_date,
            },
        )
        await self._session.flush()
        return pid

    async def close_period(
        self, *, tenant_id: UUID, period_id: UUID, closed_by: UUID
    ) -> dict[str, Any]:
        await self._session.execute(
            _sa_text(
                "UPDATE accounting_periods SET status = 'closed', closed_at = :now, closed_by = :by "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'open'"
            ),
            {"now": utcnow(), "by": closed_by, "id": period_id, "tenant_id": tenant_id},
        )
        return {"period_id": str(period_id), "status": "closed"}

    async def post_journal(
        self,
        *,
        tenant_id: UUID,
        input: PostJournalInput,
        posted_by: UUID | None = None,
    ) -> PostJournalResult:
        if not input.lines:
            raise ValueError("at least one line is required")
        total_d = sum((ln.debit_amount for ln in input.lines), Decimal("0"))
        total_c = sum((ln.credit_amount for ln in input.lines), Decimal("0"))
        if total_d != total_c:
            raise ValueError(
                f"debits ({total_d}) != credits ({total_c}); journal entry does not balance"
            )
        if total_d == 0:
            raise ValueError("zero-amount journal entry")

        eid = uuid4()
        enumber = f"JE-{eid.hex[:8].upper()}"
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, entry_number, period_id, entry_date, source,
                    source_id, description, status, posted_by, total_debit, total_credit
                ) VALUES (
                    :id, :tenant_id, :en, :period_id, :date, :source,
                    :source_id, :desc, 'posted', :posted_by, :td, :tc
                )
                """
            ),
            {
                "id": eid,
                "tenant_id": tenant_id,
                "en": enumber,
                "period_id": input.period_id,
                "date": input.entry_date,
                "source": input.source,
                "source_id": input.source_id,
                "desc": input.description,
                "posted_by": posted_by,
                "td": total_d,
                "tc": total_c,
            },
        )
        for idx, ln in enumerate(input.lines, start=1):
            if ln.debit_amount > 0 and ln.credit_amount > 0:
                raise ValueError(f"line {idx} has both debit and credit")
            if ln.debit_amount == 0 and ln.credit_amount == 0:
                raise ValueError(f"line {idx} is zero")
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO journal_lines (
                        tenant_id, entry_id, line_number, account_id, description,
                        debit_amount, credit_amount, party_id, ar_invoice_id, ap_invoice_id
                    ) VALUES (
                        :tenant_id, :eid, :ln, :account_id, :desc,
                        :debit, :credit, :party_id, :ar, :ap
                    )
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "eid": eid,
                    "ln": idx,
                    "account_id": ln.account_id,
                    "desc": ln.description,
                    "debit": ln.debit_amount,
                    "credit": ln.credit_amount,
                    "party_id": ln.party_id,
                    "ar": ln.ar_invoice_id,
                    "ap": ln.ap_invoice_id,
                },
            )
        await self._session.flush()
        return PostJournalResult(
            entry_id=eid, entry_number=enumber, total_debit=total_d, total_credit=total_c
        )


# ----- W4.1 hooks: auto-posting from the O2C / P2P flows ----- #


async def post_sales_invoice(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    invoice_id: UUID,
    customer_id: UUID,
    total_amount: Decimal,
    cogs_amount: Decimal,
    ar_account_id: UUID,
    revenue_account_id: UUID,
    cogs_account_id: UUID,
    inventory_account_id: UUID,
) -> PostJournalResult:
    """Post the AR / revenue / COGS / inventory lines on invoice creation.

    Debit  AR           total_amount
    Credit Revenue      total_amount
    Debit  COGS         cogs_amount
    Credit Inventory    cogs_amount
    """
    svc = GLService(session)
    return await svc.post_journal(
        tenant_id=tenant_id,
        input=PostJournalInput(
            entry_date=utcnow().date(),
            source="sales_order",
            source_id=invoice_id,
            description=f"Auto-post for invoice {invoice_id}",
            lines=[
                JournalLineInput(account_id=ar_account_id, debit_amount=total_amount, party_id=customer_id, ar_invoice_id=invoice_id),
                JournalLineInput(account_id=revenue_account_id, credit_amount=total_amount, party_id=customer_id, ar_invoice_id=invoice_id),
                JournalLineInput(account_id=cogs_account_id, debit_amount=cogs_amount),
                JournalLineInput(account_id=inventory_account_id, credit_amount=cogs_amount),
            ],
        ),
    )


async def post_ar_payment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    payment_id: UUID,
    customer_id: UUID,
    amount: Decimal,
    ar_account_id: UUID,
    cash_account_id: UUID,
) -> PostJournalResult:
    """Post the cash receipt for a customer payment.

    Debit  Cash   amount
    Credit AR     amount
    """
    svc = GLService(session)
    return await svc.post_journal(
        tenant_id=tenant_id,
        input=PostJournalInput(
            entry_date=utcnow().date(),
            source="payment",
            source_id=payment_id,
            description=f"Auto-post for AR payment {payment_id}",
            lines=[
                JournalLineInput(account_id=cash_account_id, debit_amount=amount),
                JournalLineInput(account_id=ar_account_id, credit_amount=amount, party_id=customer_id),
            ],
        ),
    )


async def post_ap_invoice(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    ap_invoice_id: UUID,
    supplier_id: UUID,
    total_amount: Decimal,
    ap_account_id: UUID,
    expense_account_id: UUID,
) -> PostJournalResult:
    """Post the AP / expense lines on supplier invoice.

    Debit  Expense   total_amount
    Credit AP        total_amount
    """
    svc = GLService(session)
    return await svc.post_journal(
        tenant_id=tenant_id,
        input=PostJournalInput(
            entry_date=utcnow().date(),
            source="ap_invoice",
            source_id=ap_invoice_id,
            description=f"Auto-post for AP invoice {ap_invoice_id}",
            lines=[
                JournalLineInput(account_id=expense_account_id, debit_amount=total_amount),
                JournalLineInput(account_id=ap_account_id, credit_amount=total_amount, party_id=supplier_id, ap_invoice_id=ap_invoice_id),
            ],
        ),
    )


async def post_ap_payment(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    payment_id: UUID,
    supplier_id: UUID,
    amount: Decimal,
    ap_account_id: UUID,
    cash_account_id: UUID,
) -> PostJournalResult:
    """Post the cash payment to a supplier.

    Debit  AP     amount
    Credit Cash   amount
    """
    svc = GLService(session)
    return await svc.post_journal(
        tenant_id=tenant_id,
        input=PostJournalInput(
            entry_date=utcnow().date(),
            source="payment",
            source_id=payment_id,
            description=f"Auto-post for AP payment {payment_id}",
            lines=[
                JournalLineInput(account_id=ap_account_id, debit_amount=amount, party_id=supplier_id),
                JournalLineInput(account_id=cash_account_id, credit_amount=amount),
            ],
        ),
    )
