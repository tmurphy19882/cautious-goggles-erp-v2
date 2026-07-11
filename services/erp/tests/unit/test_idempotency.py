"""Unit tests for the `_hash_idempotency_key` helper (ADOPT-1).

Verifies that:
- Same key + same tenant → same hash (idempotent).
- Same key + different tenant → different hash (no cross-tenant collision).
- Different key + same tenant → different hash.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

from shared.idempotency import _hash_idempotency_key, _hash_request_body


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("99999999-9999-9999-9999-999999999999")


def test_hash_is_stable_for_same_tenant_and_key() -> None:
    a = _hash_idempotency_key("order-2026-07-11-001", TENANT_A)
    b = _hash_idempotency_key("order-2026-07-11-001", TENANT_A)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_differs_across_tenants_for_same_key() -> None:
    """Closes the ADOPT-1 / BUG-002 cross-tenant collision class."""
    a = _hash_idempotency_key("order-2026-07-11-001", TENANT_A)
    b = _hash_idempotency_key("order-2026-07-11-001", TENANT_B)
    assert a != b


def test_hash_differs_for_different_key() -> None:
    a = _hash_idempotency_key("key-1", TENANT_A)
    b = _hash_idempotency_key("key-2", TENANT_A)
    assert a != b


def test_hash_matches_sha256_of_tenant_key_concat() -> None:
    """The implementation must match the v1 pattern byte-for-byte."""
    key = "order-2026-07-11-001"
    expected = hashlib.sha256(f"{TENANT_A}:{key}".encode("utf-8")).hexdigest()
    assert _hash_idempotency_key(key, TENANT_A) == expected


def test_request_body_hash_is_pure_sha256() -> None:
    body = b'{"hello":"world"}'
    a = _hash_request_body(body)
    b = _hash_request_body(body)
    assert a == b == hashlib.sha256(body).hexdigest()
    assert _hash_request_body(b"") == hashlib.sha256(b"").hexdigest()
