"""Time helpers for ERP v2.

Centralised so we never have to reason about timezone math in the rest
of the codebase. Use `utcnow()` everywhere instead of `datetime.utcnow()`.
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utcnow", "to_utc"]


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(tz=timezone.utc)


def to_utc(dt: datetime | None) -> datetime | None:
    """Coerce a naive or aware datetime to UTC. None passes through."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
