"""Search service for ERP v2 (MD-10 / W3+).

W3 ships:
- A `search_index` table maintained by the writers (`PartyService.create`
  etc. write to it inline; W6 swaps for an async indexer).
- A `SearchService.query` that runs a trigram-fuzzy search against
  `search_text` and returns ranked hits.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text as _sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SearchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query(
        self,
        *,
        tenant_id: UUID,
        q: str,
        entity_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fuzzy-search the search_index. Returns ranked hits."""
        if not q or not q.strip():
            return []
        limit = max(1, min(100, limit))
        params: dict[str, Any] = {"tenant_id": tenant_id, "q": q, "limit": limit}
        entity_clause = ""
        if entity_types:
            entity_clause = "AND entity_type = ANY(:entity_types)"
            params["entity_types"] = entity_types
        rows = (
            await self._session.execute(
                _sa_text(
                    f"""
                    SELECT entity_type, entity_id, title, subtitle, url, tags,
                           similarity(search_text, :q) AS score
                    FROM search_index
                    WHERE tenant_id = :tenant_id
                      {entity_clause}
                      AND search_text % :q
                    ORDER BY score DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]
