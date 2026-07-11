"""CRM core service for W5.

W5 ships:
- `LeadService` — create, qualify, convert (lead → party + opportunity)
- `OpportunityService` — create, transition stage, mark won / lost
- `QuoteService` — create, send, accept, **convert-to-SO** (closes
  O2C-15), expire
- `ActivityService` — log activities on the Party 360° timeline
- `TicketService` — create support ticket, comment, status transitions

The convert-to-SO flow calls into `o2c.sales_order.SalesOrderService.create`
with the quote's lines copied over, then sets `converted_sales_order_id`
on both the quote and the parent opportunity.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from o2c.sales_order import (
    CreateSalesOrderInput,
    SalesOrderLineInput,
    SalesOrderService,
)
from o2c.tax_fx import compute_line_totals, resolve_tax_rule
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CreateLeadInput:
    company_name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str = "manual"
    notes: str | None = None


@dataclass(slots=True)
class CreateLeadResult:
    lead_id: UUID
    status: str


class LeadService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, tenant_id: UUID, input: CreateLeadInput) -> CreateLeadResult:
        lid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO leads (id, tenant_id, source, company_name, contact_name,
                                   email, phone, status, notes)
                VALUES (:id, :tenant_id, :source, :company, :contact, :email, :phone,
                        'new', :notes)
                """
            ),
            {
                "id": lid,
                "tenant_id": tenant_id,
                "source": input.source,
                "company": input.company_name,
                "contact": input.contact_name,
                "email": input.email,
                "phone": input.phone,
                "notes": input.notes,
            },
        )
        await self._session.flush()
        return CreateLeadResult(lead_id=lid, status="new")

    async def qualify(
        self, *, tenant_id: UUID, lead_id: UUID, score: int = 50
    ) -> dict[str, Any]:
        await self._session.execute(
            _sa_text(
                "UPDATE leads SET status = 'qualified', score = :score "
                "WHERE id = :id AND tenant_id = :tenant_id"
            ),
            {"score": score, "id": lead_id, "tenant_id": tenant_id},
        )
        return {"lead_id": str(lead_id), "status": "qualified", "score": score}

    async def convert(
        self,
        *,
        tenant_id: UUID,
        lead_id: UUID,
        party_code: str,
        opportunity_name: str,
        pipeline_id: UUID,
        stage_id: UUID,
        amount: Decimal,
    ) -> dict[str, Any]:
        """Convert a qualified lead to a Party + Opportunity.

        Reads the lead, creates a party (kind=customer), then an
        opportunity in the given stage. Marks the lead as converted.
        """
        lead = (
            await self._session.execute(
                _sa_text("SELECT * FROM leads WHERE id = :id AND tenant_id = :tenant_id"),
                {"id": lead_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if lead is None:
            raise ValueError(f"lead not found: {lead_id}")
        if lead["status"] not in ("qualified", "new"):
            raise ValueError(f"lead is not convertible (status={lead['status']})")

        # Create the party.
        party_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO parties (id, tenant_id, code, name, kind, email, phone)
                VALUES (:id, :tenant_id, :code, :name, 'customer', :email, :phone)
                """
            ),
            {
                "id": party_id,
                "tenant_id": tenant_id,
                "code": party_code,
                "name": lead["company_name"],
                "email": lead["email"],
                "phone": lead["phone"],
            },
        )
        # Create the opportunity.
        opp_id = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO opportunities (
                    id, tenant_id, name, party_id, pipeline_id, stage_id,
                    amount, currency, status
                ) VALUES (
                    :id, :tenant_id, :name, :party_id, :pipeline_id, :stage_id,
                    :amount, 'USD', 'open'
                )
                """
            ),
            {
                "id": opp_id,
                "tenant_id": tenant_id,
                "name": opportunity_name,
                "party_id": party_id,
                "pipeline_id": pipeline_id,
                "stage_id": stage_id,
                "amount": amount,
            },
        )
        await self._session.execute(
            _sa_text(
                "UPDATE leads SET status = 'converted', converted_at = :now, "
                "converted_party_id = :pid, converted_opportunity_id = :oid "
                "WHERE id = :id"
            ),
            {"now": utcnow(), "pid": party_id, "oid": opp_id, "id": lead_id},
        )
        await self._session.flush()
        return {"lead_id": str(lead_id), "party_id": str(party_id), "opportunity_id": str(opp_id)}


@dataclass(slots=True)
class CreateQuoteLineInput:
    product_id: UUID
    sku: str
    description: str | None
    quantity: Decimal
    unit: str = "each"
    unit_price: Decimal = Decimal("0")
    discount_pct: Decimal = Decimal("0")
    tax_rule_id: UUID | None = None


@dataclass(slots=True)
class CreateQuoteInput:
    party_id: UUID
    opportunity_id: UUID | None = None
    currency: str = "USD"
    valid_until: Any | None = None
    notes: str | None = None
    lines: list[CreateQuoteLineInput] = field(default_factory=list)


@dataclass(slots=True)
class CreateQuoteResult:
    quote_id: UUID
    quote_number: str
    total_amount: Decimal
    line_count: int


class QuoteService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, tenant_id: UUID, input: CreateQuoteInput) -> CreateQuoteResult:
        if not input.lines:
            raise ValueError("at least one line is required")
        qid = uuid4()
        qnumber = f"Q-{qid.hex[:8].upper()}"
        subtotal = Decimal("0")
        tax_amount = Decimal("0")
        line_rows: list[dict[str, Any]] = []
        for idx, line in enumerate(input.lines, start=1):
            rule = await resolve_tax_rule(self._session, tenant_id, line.tax_rule_id)
            line_total, line_tax = compute_line_totals(
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_pct=line.discount_pct,
                tax_rate_pct=rule.rate_pct if rule else Decimal("0"),
                is_inclusive=rule.is_inclusive if rule else False,
            )
            subtotal += line_total - line_tax
            tax_amount += line_tax
            line_rows.append(
                {
                    "line_number": idx,
                    "product_id": line.product_id,
                    "sku": line.sku,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "unit_price": line.unit_price,
                    "discount_pct": line.discount_pct,
                    "tax_rule_id": line.tax_rule_id,
                    "tax_amount": line_tax,
                    "line_total": line_total,
                }
            )
        total = subtotal + tax_amount
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO quotes (
                    id, tenant_id, quote_number, opportunity_id, party_id,
                    status, currency, valid_until, subtotal, tax_amount, total_amount, notes
                ) VALUES (
                    :id, :tenant_id, :qn, :opp, :party,
                    'draft', :currency, :valid, :subtotal, :tax, :total, :notes
                )
                """
            ),
            {
                "id": qid,
                "tenant_id": tenant_id,
                "qn": qnumber,
                "opp": input.opportunity_id,
                "party": input.party_id,
                "currency": input.currency,
                "valid": input.valid_until,
                "subtotal": subtotal,
                "tax": tax_amount,
                "total": total,
                "notes": input.notes,
            },
        )
        for row in line_rows:
            await self._session.execute(
                _sa_text(
                    """
                    INSERT INTO quote_lines (
                        tenant_id, quote_id, line_number, product_id, sku,
                        description, quantity, unit, unit_price, discount_pct,
                        tax_rule_id, tax_amount, line_total
                    ) VALUES (
                        :tenant_id, :qid, :ln, :product_id, :sku,
                        :description, :quantity, :unit, :unit_price, :discount_pct,
                        :tax_rule_id, :tax_amount, :line_total
                    )
                    """
            ),
                {"tenant_id": tenant_id, "qid": qid, **row},
            )
        await self._session.flush()
        return CreateQuoteResult(
            quote_id=qid, quote_number=qnumber, total_amount=total, line_count=len(line_rows)
        )

    async def send(self, *, tenant_id: UUID, quote_id: UUID) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE quotes SET status = 'sent', sent_at = :now, updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'draft'"
            ),
            {"now": utcnow(), "id": quote_id, "tenant_id": tenant_id},
        )

    async def accept(self, *, tenant_id: UUID, quote_id: UUID) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE quotes SET status = 'accepted', accepted_at = :now, updated_at = :now "
                "WHERE id = :id AND tenant_id = :tenant_id AND status = 'sent'"
            ),
            {"now": utcnow(), "id": quote_id, "tenant_id": tenant_id},
        )

    async def convert_to_sales_order(
        self, *, tenant_id: UUID, quote_id: UUID
    ) -> dict[str, Any]:
        """O2C-15: convert an accepted quote to a sales order."""
        quote = (
            await self._session.execute(
                _sa_text("SELECT * FROM quotes WHERE id = :id AND tenant_id = :tenant_id"),
                {"id": quote_id, "tenant_id": tenant_id},
            )
        ).mappings().first()
        if quote is None:
            raise ValueError(f"quote not found: {quote_id}")
        if quote["status"] != "accepted":
            raise ValueError(
                f"quote must be 'accepted' to convert (current: {quote['status']})"
            )
        # Pull the lines.
        line_rows = (
            await self._session.execute(
                _sa_text(
                    "SELECT * FROM quote_lines WHERE quote_id = :id "
                    "AND tenant_id = :tenant_id ORDER BY line_number"
                ),
                {"id": quote_id, "tenant_id": tenant_id},
            )
        ).mappings().all()
        # Hand off to the SO service.
        so_svc = SalesOrderService(self._session)
        so_result = await so_svc.create(
            tenant_id=tenant_id,
            input=CreateSalesOrderInput(
                customer_id=quote["party_id"],
                currency=quote["currency"],
                notes=f"Converted from quote {quote['quote_number']}",
                lines=[
                    SalesOrderLineInput(
                        product_id=line["product_id"],
                        sku=line["sku"],
                        description=line["description"],
                        quantity=line["quantity"],
                        unit=line["unit"],
                        unit_price=line["unit_price"],
                        discount_pct=line["discount_pct"],
                        tax_rule_id=line["tax_rule_id"],
                    )
                    for line in line_rows
                ],
            ),
        )
        # Mark the quote + (optional) opportunity as converted.
        await self._session.execute(
            _sa_text(
                "UPDATE quotes SET status = 'converted', converted_at = :now, "
                "converted_sales_order_id = :soid, updated_at = :now "
                "WHERE id = :id"
            ),
            {
                "now": utcnow(),
                "soid": so_result.order_id,
                "id": quote_id,
            },
        )
        if quote["opportunity_id"] is not None:
            await self._session.execute(
                _sa_text(
                    "UPDATE opportunities SET converted_sales_order_id = :soid, "
                    "status = 'won', closed_at = :now, close_reason = 'quote accepted', "
                    "updated_at = :now WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {
                    "soid": so_result.order_id,
                    "now": utcnow(),
                    "id": quote["opportunity_id"],
                    "tenant_id": tenant_id,
                },
            )
        return {
            "quote_id": str(quote_id),
            "sales_order_id": str(so_result.order_id),
            "sales_order_number": so_result.order_number,
            "status": "converted",
        }
