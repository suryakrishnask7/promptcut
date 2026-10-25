"""
src/promptcut/api/webhook.py

IncuBrix Integration Standard #3 (Signed Webhooks):
Dispatches callback webhooks signed with HMAC-SHA256 in X-IncuBrix-Signature header.
Supports limited retries (up to 3 attempts) with timeout handling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Callable
import urllib.request

logger = logging.getLogger("promptcut.api.webhook")

DEFAULT_SECRET = os.getenv("INCUBRIX_WEBHOOK_SECRET", "incubrix_secret_key_default")


def sign_webhook_payload(payload_bytes: bytes, secret: str = DEFAULT_SECRET) -> str:
    """Compute HMAC-SHA256 hex digest for webhook payload."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def _default_http_post(url: str, data: bytes, headers: dict[str, str], timeout: float = 5.0) -> Any:
    """Send POST request via standard library urllib."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp


def dispatch_webhook(
    callback_url: str,
    payload: dict[str, Any],
    secret: str = DEFAULT_SECRET,
    max_retries: int = 3,
    timeout: float = 5.0,
    poster: Callable[..., Any] | None = None,
) -> bool:
    """
    Dispatch a webhook notification with HMAC-SHA256 signature in X-IncuBrix-Signature.
    Retries up to max_retries on connection or transient HTTP errors.
    """
    if not callback_url or not callback_url.startswith(("http://", "https://")):
        return False

    post_fn = poster or _default_http_post
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    signature = sign_webhook_payload(payload_bytes, secret)

    headers = {
        "Content-Type": "application/json",
        "X-IncuBrix-Signature": signature,
        "User-Agent": "PromptCut-Engine/0.3.0",
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = post_fn(callback_url, data=payload_bytes, headers=headers, timeout=timeout)
            status_code = getattr(resp, "status_code", getattr(resp, "status", 200))
            if 200 <= status_code < 300:
                logger.info(f"Webhook dispatched successfully to {callback_url} [attempt={attempt}]")
                return True
            logger.warning(f"Webhook received status {status_code} from {callback_url} [attempt={attempt}]")
        except Exception as e:
            logger.warning(f"Webhook delivery failed to {callback_url} [attempt={attempt}/{max_retries}]: {e}")

        if attempt < max_retries:
            time.sleep(0.2 * attempt)

    return False
