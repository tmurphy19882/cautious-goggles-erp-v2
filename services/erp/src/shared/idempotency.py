"""Idempotency for ERP v2.

Every mutating route requires an `Idempotency-Key` header. The key, paired
with the request hash, is stored in `idempotency_keys`. A repeat request
with the same key returns the original response (status + body) without
re-running the handler.

Scope:

- `tenant_id` + `key` is the dedup key. Two tenants can use the same key.
- `request_hash` ensures the same key isn't reused with a *different* body.
- `response_body` + `status` are replayed verbatim on a hit.
- `expires_at` is the TTL (default 24h).

Usage:

    from fastapi import Depends
    from shared.idempotency import IdempotencyMiddleware, get_idempotency_store

    app.add_middleware(IdempotencyMiddleware, store=...)
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy import Column, DateTime, Integer, JSON, String, Table, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from shared.db import Base
from shared.errors import (
    IdempotencyKeyMismatchError,
    IdempotencyKeyRequiredError,
)
from shared.schemas import utcnow
from shared.tenant import current_tenant_id

logger = logging.getLogger(__name__)


IDEMPOTENCY_HEADER = "Idempotency-Key"
DEFAULT_TTL = timedelta(hours=24)


def _hash_request_body(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


class IdempotencyRecord:
    """In-memory shape; matches the `idempotency_keys` table in W0.

    Schema (full migration lands in a later wave; v1 already has the table
    in `services/erp/src/models.py`):

        tenant_id        UUID
        key_hash         TEXT
        request_hash     TEXT
        response_body    JSONB
        status           INT
        expires_at       TIMESTAMPTZ
    """

    __slots__ = ("tenant_id", "key_hash", "request_hash", "response_body", "status", "expires_at")

    def __init__(
        self,
        tenant_id: UUID,
        key_hash: str,
        request_hash: str,
        response_body: dict[str, Any],
        status: int,
        expires_at: datetime,
    ) -> None:
        self.tenant_id = tenant_id
        self.key_hash = key_hash
        self.request_hash = request_hash
        self.response_body = response_body
        self.status = status
        self.expires_at = expires_at


# --- SQLAlchemy table reflection (so the store works without a hard model import) ---
# We declare a lightweight `Table` here rather than an ORM model so the store
# can be used in W0 before the W1+ migrations add the formal table.
IdempotencyKeyTable = Table(
    "idempotency_keys",
    Base.metadata,
    Column("tenant_id", String, primary_key=True),
    Column("key_hash", String(64), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("response_body", JSON, nullable=False),
    Column("status", Integer, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    extend_existing=True,
)


def _select_idempotency(tenant_id: UUID, key_hash: str):
    return select(
        IdempotencyKeyTable.c.tenant_id,
        IdempotencyKeyTable.c.key_hash,
        IdempotencyKeyTable.c.request_hash,
        IdempotencyKeyTable.c.response_body,
        IdempotencyKeyTable.c.status,
        IdempotencyKeyTable.c.expires_at,
    ).where(
        IdempotencyKeyTable.c.tenant_id == str(tenant_id),
        IdempotencyKeyTable.c.key_hash == key_hash,
        IdempotencyKeyTable.c.expires_at > utcnow(),
    )


class IdempotencyStore:
    """Abstract interface so we can swap to Redis later without changing callers."""

    async def get(self, tenant_id: UUID, key_hash: str) -> IdempotencyRecord | None: ...
    async def put(self, record: IdempotencyRecord) -> None: ...
    async def delete_expired(self) -> int: ...


class DbIdempotencyStore(IdempotencyStore):
    """Postgres-backed implementation using the shared session factory.

    Uses raw SQL via SQLAlchemy core (no ORM model required) so the store
    stays independent of any module's models. The W1+ migrations add the
    `idempotency_keys` table.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, tenant_id: UUID, key_hash: str) -> IdempotencyRecord | None:
        async with self._sf() as session:
            row = await session.execute(_select_idempotency(tenant_id, key_hash))
            r = row.first()
            if r is None:
                return None
            return IdempotencyRecord(
                tenant_id=r.tenant_id,
                key_hash=r.key_hash,
                request_hash=r.request_hash,
                response_body=r.response_body,
                status=r.status,
                expires_at=r.expires_at,
            )

    async def put(self, record: IdempotencyRecord) -> None:
        async with self._sf() as session:
            dialect = session.bind.dialect.name if session.bind else "postgresql"
            insert_stmt = (
                pg_insert(IdempotencyKeyTable)
                if dialect == "postgresql"
                else sqlite_insert(IdempotencyKeyTable)
            )
            stmt = insert_stmt.values(
                tenant_id=record.tenant_id,
                key_hash=record.key_hash,
                request_hash=record.request_hash,
                response_body=record.response_body,
                status=record.status,
                expires_at=record.expires_at,
            )
            # On conflict: do nothing — the original writer wins.
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["tenant_id", "key_hash"],
            )
            await session.execute(stmt)
            await session.commit()

    async def delete_expired(self) -> int:
        async with self._sf() as session:
            result = await session.execute(
                delete(IdempotencyKeyTable).where(IdempotencyKeyTable.c.expires_at < utcnow())
            )
            await session.commit()
            return result.rowcount or 0


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces Idempotency-Key on mutating routes.

    Caches replays in the store. If a request with the same key + same body
    comes in within the TTL, returns the original response.

    NOTE: this middleware reads the request body and therefore consumes
    the body stream. Handlers that need to read the body again will
    receive an empty stream — they must call `await request.body()` only
    once. Pydantic + FastAPI handle this correctly when the body is read
    by the route's parameter binding.
    """

    MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app, *, store: IdempotencyStore, ttl: timedelta = DEFAULT_TTL) -> None:
        super().__init__(app)
        self._store = store
        self._ttl = ttl

    async def dispatch(self, request: Request, call_next):
        if request.method not in self.MUTATING_METHODS:
            return await call_next(request)

        key = request.headers.get(IDEMPOTENCY_HEADER)
        if not key:
            # Allow callers to opt out for non-state-changing requests by
            # skipping the check on paths registered as idempotent-by-design.
            if self._is_idempotent_path(request.url.path):
                return await call_next(request)
            return JSONResponse(
                status_code=IdempotencyKeyRequiredError.status_code,
                content={
                    "code": IdempotencyKeyRequiredError.code,
                    "message": f"{IDEMPOTENCY_HEADER} header required for mutating requests",
                },
            )

        tenant_id_str = current_tenant_id(request)
        if tenant_id_str is None:
            # Tenant middleware should have rejected already; fail loud.
            return JSONResponse(
                status_code=400,
                content={"code": "tenant_required", "message": "x-tenant-id header required"},
            )
        tenant_id = UUID(str(tenant_id_str))

        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        body = await request.body()
        request_hash = _hash_request_body(body)

        existing = await self._store.get(tenant_id, key_hash)
        if existing is not None:
            if existing.request_hash != request_hash:
                return JSONResponse(
                    status_code=IdempotencyKeyMismatchError.status_code,
                    content={
                        "code": IdempotencyKeyMismatchError.code,
                        "message": "Idempotency-Key reused with a different request body",
                    },
                )
            return JSONResponse(status_code=existing.status, content=existing.response_body)

        # First time: invoke the handler, capture the response, persist.
        response = await call_next(request)
        # Only persist 2xx responses; let 4xx/5xx be retried.
        if 200 <= response.status_code < 300:
            response_body_bytes = b""
            async for chunk in response.body_iterator:
                response_body_bytes += chunk
            try:
                response_payload = json.loads(response_body_bytes or b"null")
            except json.JSONDecodeError:
                response_payload = {"_raw": response_body_bytes.decode("utf-8", errors="replace")}

            await self._store.put(
                IdempotencyRecord(
                    tenant_id=tenant_id,
                    key_hash=key_hash,
                    request_hash=request_hash,
                    response_body=response_payload,
                    status=response.status_code,
                    expires_at=utcnow() + self._ttl,
                )
            )
            return Response(
                content=response_body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response

    @staticmethod
    def _is_idempotent_path(path: str) -> bool:
        # Health/ready are GETs, never reach here, but keep the hook
        # for future opt-outs.
        return False
