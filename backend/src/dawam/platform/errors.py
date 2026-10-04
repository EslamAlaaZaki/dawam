"""The one error format of the API: ``{"error": {"code", "message", "details"}}``.

Module code signals an expected failure by raising ``ApiError`` with a stable,
machine-readable ``code`` (snake_case). ``install_error_handlers`` renders that, and
every other error (unknown route, validation, ``HTTPException``, unhandled exception),
in the same shape. Unhandled exceptions are rendered by the request middleware in
``dawam.platform.request_context`` so the response still carries the request id.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    code: str = Field(description="Stable, machine-readable error code, e.g. `not_found`.")
    message: str = Field(description="Human-readable explanation.")
    details: dict[str, Any] = Field(description="Code-specific extras; may be empty.")


class ErrorResponse(BaseModel):
    error: ErrorBody


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Validation error"},
    "default": {"model": ErrorResponse, "description": "Error"},
}
"""OpenAPI ``responses`` for every versioned route, so the client knows the error type."""


class ApiError(Exception):
    """An expected failure with a stable error code, rendered as an error response."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers
        """Extra response headers, e.g. ``Retry-After``."""


_CODES_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def code_for_status(status_code: int) -> str:
    return _CODES_BY_STATUS.get(status_code, f"http_{status_code}")


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or {}))
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code, headers=headers)


def internal_error_response() -> JSONResponse:
    return error_response(500, "internal_error", "An unexpected error occurred.")


def _phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.code, exc.message, exc.details, headers=exc.headers)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    message = exc.detail if isinstance(exc.detail, str) else _phrase(exc.status_code)
    details = exc.detail if isinstance(exc.detail, dict) else None
    return error_response(
        exc.status_code,
        code_for_status(exc.status_code),
        message,
        details,
        headers=getattr(exc, "headers", None),
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = [
        {
            "loc": list(err.get("loc", ())),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    return error_response(422, "validation_error", "The request is not valid.", {"fields": fields})


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
