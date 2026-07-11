"""Error envelope and exception types for ERP v2.

Every error returned to the client uses the same JSON shape:

    {
        "code": "permission_denied",
        "message": "User lacks permission erp.so.create",
        "details": {...},
        "trace_id": "abc123"
    }

This is registered as the global exception handler in `api/main.py`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    trace_id: str | None = None


class ApiError(Exception):
    """Base for everything that should return a structured error envelope.

    Subclasses set `status_code` and `code`; the global handler in
    `api/main.py` converts to JSON.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class ValidationFailedError(ApiError):
    status_code = 422
    code = "validation_failed"


class ConflictError(ApiError):
    status_code = 409
    code = "conflict"


class PermissionDeniedError(ApiError):
    status_code = 403
    code = "permission_denied"


class UnauthenticatedError(ApiError):
    status_code = 401
    code = "unauthenticated"


class TenantRequiredError(ApiError):
    status_code = 400
    code = "tenant_required"


class IdempotencyKeyRequiredError(ApiError):
    status_code = 400
    code = "idempotency_key_required"


class IdempotencyKeyMismatchError(ApiError):
    status_code = 409
    code = "idempotency_key_mismatch"


def _trace_id_from(request: Request) -> str | None:
    """Pull the current trace id from the request state if OTel set one."""
    return getattr(request.state, "trace_id", None)


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers for ApiError, RequestValidationError, and Exception."""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        envelope = ErrorEnvelope(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            trace_id=_trace_id_from(request),
        )
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(exclude_none=True))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        envelope = ErrorEnvelope(
            code=ValidationFailedError.code,
            message="Request validation failed",
            details={"errors": exc.errors()},
            trace_id=_trace_id_from(request),
        )
        return JSONResponse(
            status_code=ValidationFailedError.status_code,
            content=envelope.model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s", exc)
        envelope = ErrorEnvelope(
            code="internal_error",
            message="An unexpected error occurred",
            trace_id=_trace_id_from(request),
        )
        return JSONResponse(
            status_code=500,
            content=envelope.model_dump(exclude_none=True),
        )
