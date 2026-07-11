"""Unit tests for the idempotency key hashing helper."""
from __future__ import annotations

import hashlib

from shared.idempotency import (
    IDEMPOTENCY_HEADER,
    _hash_request_body,
)


def test_hash_is_stable_for_same_body() -> None:
    body = b'{"hello":"world"}'
    a = _hash_request_body(body)
    b = _hash_request_body(body)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_changes_with_body() -> None:
    a = _hash_request_body(b"{}")
    b = _hash_request_body(b'{"a":1}')
    assert a != b


def test_hash_handles_empty_body() -> None:
    h = _hash_request_body(b"")
    assert h == hashlib.sha256(b"").hexdigest()


def test_header_constant() -> None:
    assert IDEMPOTENCY_HEADER == "Idempotency-Key"
