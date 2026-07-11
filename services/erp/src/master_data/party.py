"""Party service for W3.

W1's `master_data/customers` was a thin wrapper that piggybacked on
the `users` table. W3 ships the real model:

- `parties` (kind = customer | vendor | carrier | employee | internal_org)
- `party_contacts`
- `party_addresses`
- `party_tax_ids`

W3 keeps the W1 customer endpoint as a compatibility shim:
`POST /master-data/customers` creates a User + a Party(kind=customer)
behind the scenes. W5+ deprecates the User-as-customer pattern.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.outbox import OutboxStore
from shared.schemas import utcnow

logger = logging.getLogger(__name__)


KIND_CUSTOMER = "customer"
KIND_VENDOR = "vendor"
KIND_CARRIER = "carrier"
KIND_EMPLOYEE = "employee"
KIND_INTERNAL = "internal_org"


@dataclass(slots=True)
class CreatePartyInput:
    code: str
    name: str
    kind: str
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    external_refs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CreatePartyResult:
    party_id: UUID
    code: str
    kind: str


class PartyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = OutboxStore(session)

    async def create(self, *, tenant_id: UUID, input: CreatePartyInput) -> CreatePartyResult:
        if input.kind not in (KIND_CUSTOMER, KIND_VENDOR, KIND_CARRIER, KIND_EMPLOYEE, KIND_INTERNAL):
            raise ValueError(f"invalid party kind: {input.kind}")
        pid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO parties (
                    id, tenant_id, code, name, kind, email, phone, website, external_refs
                ) VALUES (
                    :id, :tenant_id, :code, :name, :kind, :email, :phone, :website, :external_refs
                )
                """
            ),
            {
                "id": pid,
                "tenant_id": tenant_id,
                "code": input.code,
                "name": input.name,
                "kind": input.kind,
                "email": input.email,
                "phone": input.phone,
                "website": input.website,
                "external_refs": input.external_refs,
            },
        )
        # Update the search index (W3 ships a synchronous indexer; W6
        # swaps for an async worker).
        search_text = " ".join(filter(None, [input.name, input.email, input.phone, input.code, input.kind]))
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO search_index (
                    tenant_id, entity_type, entity_id, title, subtitle, search_text, url, tags
                ) VALUES (
                    :tenant_id, 'party', :id, :title, :subtitle, :text, :url, :tags::jsonb
                )
                ON CONFLICT (tenant_id, entity_type, entity_id)
                DO UPDATE SET title = EXCLUDED.title, search_text = EXCLUDED.search_text, updated_at = now()
                """
            ),
            {
                "tenant_id": tenant_id,
                "id": pid,
                "title": input.name,
                "subtitle": f"{input.kind} · {input.code}",
                "text": search_text,
                "url": f"/dashboard/parties/{pid}",
                "tags": f'["{input.kind}"]',
            },
        )
        await self._session.flush()
        return CreatePartyResult(party_id=pid, code=input.code, kind=input.kind)

    async def add_contact(
        self,
        *,
        tenant_id: UUID,
        party_id: UUID,
        full_name: str,
        role: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        is_primary: bool = False,
    ) -> UUID:
        cid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO party_contacts (
                    id, tenant_id, party_id, full_name, role, email, phone, is_primary
                ) VALUES (
                    :id, :tenant_id, :party_id, :name, :role, :email, :phone, :is_primary
                )
                """
            ),
            {
                "id": cid,
                "tenant_id": tenant_id,
                "party_id": party_id,
                "name": full_name,
                "role": role,
                "email": email,
                "phone": phone,
                "is_primary": is_primary,
            },
        )
        await self._session.flush()
        return cid

    async def add_address(
        self,
        *,
        tenant_id: UUID,
        party_id: UUID,
        kind: str,
        line1: str | None = None,
        city: str | None = None,
        region: str | None = None,
        postal_code: str | None = None,
        country_code: str | None = None,
        is_primary: bool = False,
    ) -> UUID:
        aid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO party_addresses (
                    id, tenant_id, party_id, kind, line1, city, region, postal_code,
                    country_code, is_primary
                ) VALUES (
                    :id, :tenant_id, :party_id, :kind, :line1, :city, :region, :postal,
                    :country, :is_primary
                )
                """
            ),
            {
                "id": aid,
                "tenant_id": tenant_id,
                "party_id": party_id,
                "kind": kind,
                "line1": line1,
                "city": city,
                "region": region,
                "postal": postal_code,
                "country": country_code,
                "is_primary": is_primary,
            },
        )
        await self._session.flush()
        return aid

    async def add_tax_id(
        self,
        *,
        tenant_id: UUID,
        party_id: UUID,
        tax_id_type: str,
        tax_id: str,
        country_code: str | None = None,
    ) -> UUID:
        tid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO party_tax_ids (
                    id, tenant_id, party_id, tax_id_type, tax_id, country_code
                ) VALUES (
                    :id, :tenant_id, :party_id, :type, :tax, :country
                )
                """
            ),
            {
                "id": tid,
                "tenant_id": tenant_id,
                "party_id": party_id,
                "type": tax_id_type,
                "tax": tax_id,
                "country": country_code,
            },
        )
        await self._session.flush()
        return tid
