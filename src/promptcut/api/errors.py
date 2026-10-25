"""
src/promptcut/api/errors.py

IncuBrix Integration Standard #5 (Clear Errors) implementation.
Every HTTP error (including unhandled exceptions and validation errors) returns:
{
    "error": {
        "code": "UPPER_SNAKE_CASE",
        "message": "...",
        "retryable": bool,
        "details": {}
    }
}
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("promptcut.api.errors")

STATUS_CODE_TO_CODE = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    408: "REQUEST_TIMEOUT",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_SERVER_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


class AppError(Exception):
    """Domain/API error with explicit code, message, retryable flag and details."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


def format_error_response(
    code: str,
    message: str,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Produce standard JSON error response."""
    payload = {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
    }
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    """Attach standard error handlers to FastAPI application."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        headers = {"X-Correlation-Id": correlation_id} if correlation_id else None
        return format_error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            retryable=exc.retryable,
            details=exc.details,
            headers=headers,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        headers = {"X-Correlation-Id": correlation_id} if correlation_id else None

        # Check if detail was passed as dict or string
        code = STATUS_CODE_TO_CODE.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = str(exc.detail)
        details: dict[str, Any] = {}

        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", code)
            message = exc.detail.get("message", message)
            details = exc.detail.get("details", details)

        # 5xx errors are typically retryable; 4xx are not (except 408 / 429)
        retryable = exc.status_code in (408, 429, 502, 503, 504)

        return format_error_response(
            code=code,
            message=message,
            status_code=exc.status_code,
            retryable=retryable,
            details=details,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        headers = {"X-Correlation-Id": correlation_id} if correlation_id else None

        formatted_errors = []
        for err in exc.errors():
            loc = " -> ".join(str(item) for item in err.get("loc", []))
            formatted_errors.append({"field": loc, "issue": err.get("msg", "")})

        return format_error_response(
            code="VALIDATION_ERROR",
            message="Request validation failed. Check request payload format.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            retryable=False,
            details={"errors": formatted_errors},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", "unknown")
        logger.exception(f"Unhandled exception during request processing [correlation_id={correlation_id}]: {exc}")
        headers = {"X-Correlation-Id": correlation_id} if correlation_id != "unknown" else None
        return format_error_response(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal error occurred. Please retry.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            retryable=True,
            details={"error_type": type(exc).__name__},
            headers=headers,
        )
