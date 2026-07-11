"""Unit tests for `ObservabilityMiddleware` shape.

The middleware itself is hard to unit-test in isolation (it wraps
BaseHTTPMiddleware with the full request lifecycle), so we test the
small pure helper it uses: `_parse_tenant`.
"""
from __future__ import annotations

from uuid import UUID

from observability.middleware import _parse_tenant


def test_parse_tenant_none_when_missing() -> None:
    assert _parse_tenant(None) is None
    assert _parse_tenant("") is None


def test_parse_tenant_valid_uuid() -> None:
    raw = "11111111-1111-1111-1111-111111111111"
    assert _parse_tenant(raw) == UUID(raw)


def test_parse_tenant_malformed_returns_none() -> None:
    """A malformed UUID in the header is dropped, not raised.

    The 422 surface is the Pydantic dep that consumes the tenant; the
    middleware's job is to *not* crash on a bad header and to expose
    `request.state.tenant_id = None` so the dep raises a clean
    `TenantRequiredError`.
    """
    assert _parse_tenant("not-a-uuid") is None
    assert _parse_tenant("12345") is None
