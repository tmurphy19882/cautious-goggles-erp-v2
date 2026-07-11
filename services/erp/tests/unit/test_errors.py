"""Unit tests for the error envelope and exception types."""
from __future__ import annotations

from shared.errors import (
    ApiError,
    ConflictError,
    ErrorEnvelope,
    NotFoundError,
    PermissionDeniedError,
    TenantRequiredError,
    UnauthenticatedError,
    ValidationFailedError,
)


def test_api_error_defaults() -> None:
    err = ApiError("boom")
    assert err.status_code == 500
    assert err.code == "internal_error"
    assert err.message == "boom"
    assert err.details == {}


def test_subclass_overrides() -> None:
    cases = [
        (NotFoundError("x"), 404, "not_found"),
        (ConflictError("x"), 409, "conflict"),
        (PermissionDeniedError("x"), 403, "permission_denied"),
        (UnauthenticatedError("x"), 401, "unauthenticated"),
        (TenantRequiredError("x"), 400, "tenant_required"),
        (ValidationFailedError("x"), 422, "validation_failed"),
    ]
    for err, status, code in cases:
        assert err.status_code == status
        assert err.code == code


def test_envelope_serialises() -> None:
    env = ErrorEnvelope(
        code="not_found",
        message="missing",
        details={"id": "abc"},
        trace_id="trace-1",
    )
    payload = env.model_dump(exclude_none=True)
    assert payload == {
        "code": "not_found",
        "message": "missing",
        "details": {"id": "abc"},
        "trace_id": "trace-1",
    }


def test_envelope_omits_none() -> None:
    env = ErrorEnvelope(code="conflict", message="dup")
    payload = env.model_dump(exclude_none=True)
    assert payload == {"code": "conflict", "message": "dup"}
    assert "details" not in payload
    assert "trace_id" not in payload
