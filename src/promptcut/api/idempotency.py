"""
src/promptcut/api/idempotency.py

IncuBrix Integration Standard #4 (Safe Retries):
- Idempotency-Key header on POST endpoints (/sessions, /instructions, /render)
- Same key + same body returns the stored response and does not re-execute
- Same key + different body returns 409 IDEMPOTENCY_KEY_REUSED
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple
from fastapi import Request, Response
from promptcut.api.errors import format_error_response


class IdempotencyRecord(NamedTuple):
    path: str
    body_hash: str
    status_code: int
    headers: dict[str, str]
    body: bytes


# In-memory storage for idempotency records: key -> IdempotencyRecord
_IDEMPOTENCY_STORE: dict[str, IdempotencyRecord] = {}


def clear_idempotency_store() -> None:
    """Clear cached idempotency records (useful for test isolation)."""
    _IDEMPOTENCY_STORE.clear()


def is_idempotency_target_path(path: str) -> bool:
    """Check if the path is one of the target endpoints for idempotency handling."""
    normalized = path.rstrip("/")
    if normalized.endswith("/sessions") or "/sessions" in normalized:
        return True
    if normalized.endswith("/instructions") or "/instructions" in normalized:
        return True
    if normalized.endswith("/render") or "/render" in normalized:
        return True
    return False


async def idempotency_middleware(request: Request, call_next) -> Response:
    """
    Middleware implementing safe retries via Idempotency-Key.
    """
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")

    # Only process POST requests with an Idempotency-Key on target paths
    if request.method != "POST" or not idempotency_key or not is_idempotency_target_path(request.url.path):
        return await call_next(request)

    # Consume and cache body
    body_bytes = await request.body()
    body_hash = hashlib.sha256(body_bytes).hexdigest()

    # Check if key was previously used
    if idempotency_key in _IDEMPOTENCY_STORE:
        record = _IDEMPOTENCY_STORE[idempotency_key]
        if record.body_hash == body_hash:
            # Replay stored response
            resp_headers = dict(record.headers)
            resp_headers["X-Idempotent-Replay"] = "true"
            # Ensure content-length is recomputed by Starlette
            resp_headers.pop("content-length", None)
            resp_headers.pop("Content-Length", None)
            return Response(
                content=record.body,
                status_code=record.status_code,
                headers=resp_headers,
                media_type=resp_headers.get("content-type", "application/json"),
            )
        else:
            # Conflict: same key used with different payload
            return format_error_response(
                code="IDEMPOTENCY_KEY_REUSED",
                message=f"Idempotency key '{idempotency_key}' has already been used with a different request payload.",
                status_code=409,
                retryable=False,
                details={"key": idempotency_key},
            )

    # Execute original request
    response = await call_next(request)

    # Cache successful / client responses (2xx / 4xx except 5xx internal errors)
    if response.status_code < 500:
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk

        resp_headers = dict(response.headers)
        _IDEMPOTENCY_STORE[idempotency_key] = IdempotencyRecord(
            path=request.url.path,
            body_hash=body_hash,
            status_code=response.status_code,
            headers=resp_headers,
            body=resp_body,
        )

        resp_headers.pop("content-length", None)
        resp_headers.pop("Content-Length", None)
        return Response(
            content=resp_body,
            status_code=response.status_code,
            headers=resp_headers,
            media_type=resp_headers.get("content-type", "application/json"),
        )

    return response
