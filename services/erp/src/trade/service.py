"""W9 — Trade compliance services.

- `HTSResolver` — keyword-based HTS auto-resolver. Loads the
  tenant's `hts_codes` (cached from the public HTS schedule) and
  scores product descriptions against `keywords`. W9.1 swaps in a
  real tariff-engine lookup (Crossref, Avalara, etc.).
- `FTZService` — admit, remove, weekly-inventory report. Closes
  TR-3.
- `ScreeningService` — re-screen a party on update against the
  W9 stub OFAC SDN list (W9 ships a 3-row fixture; W9.1 swaps in
  the real feed). Closes TR-4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.schemas import utcnow

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# HTS                                                                     #
# --------------------------------------------------------------------- #


@dataclass(slots=True, frozen=True)
class HTSMatch:
    code: str
    description: str
    score: float


class HTSResolver:
    """Naive keyword-based HTS auto-resolver. Real one in W9.1."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load(self, tenant_id: UUID) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                _sa_text(
                    "SELECT code, description, keywords, duty_rate, unit "
                    "FROM hts_codes WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def resolve(
        self, *, tenant_id: UUID, description: str
    ) -> list[HTSMatch]:
        description_lc = description.lower()
        codes = await self._load(tenant_id)
        scored: list[HTSMatch] = []
        for c in codes:
            keywords = [k.lower() for k in (c.get("keywords") or [])]
            if not keywords:
                continue
            hits = sum(1 for k in keywords if k in description_lc)
            if hits == 0:
                continue
            score = hits / max(1, len(keywords))
            scored.append(
                HTSMatch(
                    code=c["code"],
                    description=c["description"],
                    score=score,
                )
            )
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:5]


# --------------------------------------------------------------------- #
# FTZ                                                                     #
# --------------------------------------------------------------------- #


class FTZService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def admit(
        self,
        *,
        tenant_id: UUID,
        entry_number: str,
        zone_id: str,
        admission_date: str,
        hts_code: str,
        quantity: Decimal,
        value: Decimal,
        unit: str | None = None,
        currency: str = "USD",
        party_id: UUID | None = None,
    ) -> UUID:
        fid = uuid4()
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO ftz_entries (
                    id, tenant_id, entry_number, zone_id, status, admission_date,
                    hts_code, quantity, unit, value, currency, party_id
                ) VALUES (
                    :id, :tenant_id, :en, :zid, 'admitted', :adate,
                    :hts, :qty, :unit, :val, :cur, :pid
                )
                """
            ),
            {
                "id": fid, "tenant_id": tenant_id, "en": entry_number,
                "zid": zone_id, "adate": admission_date, "hts": hts_code,
                "qty": quantity, "unit": unit, "val": value, "cur": currency,
                "pid": party_id,
            },
        )
        await self._session.flush()
        return fid

    async def remove(
        self, *, tenant_id: UUID, entry_id: UUID, removal_date: str
    ) -> None:
        await self._session.execute(
            _sa_text(
                "UPDATE ftz_entries SET status = 'removed', removal_date = :rd "
                "WHERE id = :id AND tenant_id = :tid"
            ),
            {"rd": removal_date, "id": entry_id, "tid": tenant_id},
        )
        await self._session.flush()

    async def weekly_inventory(
        self, *, tenant_id: UUID, as_of: str
    ) -> list[dict[str, Any]]:
        rows = (
            await self._session.execute(
                _sa_text(
                    "SELECT zone_id, COUNT(*) AS entries, COALESCE(SUM(value), 0) AS value "
                    "FROM ftz_entries WHERE tenant_id = :tid AND status = 'admitted' "
                    "GROUP BY zone_id ORDER BY zone_id"
                ),
                {"tid": tenant_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------- #
# Screening                                                               #
# --------------------------------------------------------------------- #


# W9 stub list — the W9.1 swap downloads the real OFAC SDN list.
_STUB_SDN: list[dict[str, Any]] = [
    {"name": "John Doe Sanctioned", "country": "XX"},
    {"name": "Acme Bad Corp", "country": "YY"},
    {"name": "Globex Denied", "country": "ZZ"},
]


class ScreeningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def screen(
        self,
        *,
        tenant_id: UUID,
        party_id: UUID,
        party_name: str,
        source: str = "ofac_sdn",
    ) -> UUID:
        name_lc = party_name.lower()
        matched_entry: dict[str, Any] | None = None
        matched = False
        score = Decimal("0.00")
        for entry in _STUB_SDN:
            entry_name_lc = entry["name"].lower()
            if entry_name_lc in name_lc or name_lc in entry_name_lc:
                matched = True
                score = Decimal("0.95")
                matched_entry = entry
                break
        sid = uuid4()
        import json
        await self._session.execute(
            _sa_text(
                """
                INSERT INTO screening_snapshots (
                    id, tenant_id, party_id, source, matched, match_score, matched_entry
                ) VALUES (
                    :id, :tenant_id, :pid, :src, :matched, :score, :entry::jsonb
                )
                """
            ),
            {
                "id": sid, "tenant_id": tenant_id, "pid": party_id,
                "src": source, "matched": matched, "score": score,
                "entry": json.dumps(matched_entry) if matched_entry else None,
            },
        )
        await self._session.flush()
        return sid

    async def latest(
        self, *, tenant_id: UUID, party_id: UUID
    ) -> dict[str, Any] | None:
        row = (
            await self._session.execute(
                _sa_text(
                    "SELECT source, matched, match_score, matched_entry, screened_at "
                    "FROM screening_snapshots WHERE tenant_id = :tid AND party_id = :pid "
                    "ORDER BY screened_at DESC LIMIT 1"
                ),
                {"tid": tenant_id, "pid": party_id},
            )
        ).mappings().first()
        return dict(row) if row else None
