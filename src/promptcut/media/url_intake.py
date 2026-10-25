"""
src/promptcut/media/url_intake.py

IncuBrix Integration Standard #5 (Video Input by URL):
- Accept video input by URL (http / https only)
- Block private / loopback / link-local IPs (also after redirects)
- Size limits (max 500 MB default) and time limits (timeout 30s)
- Disallow localhost, 127.0.0.1, 169.254.x.x, RFC 1918 subnets
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
import socket
import urllib.parse
import urllib.request
from typing import Any

from promptcut.api.errors import AppError

MAX_VIDEO_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30.0


class SSRFProtectionError(AppError):
    """Raised when a URL attempts to access prohibited internal or private network targets."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="SSRF_BLOCKED",
            message=message,
            status_code=400,
            retryable=False,
            details=details,
        )


def validate_url_target(url: str) -> None:
    """
    Validate that a given URL uses http/https and does not resolve to
    loopback, link-local, private, or reserved network addresses.
    """
    parsed = urllib.parse.urlparse(url)

    # 1. Scheme check: only http and https
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFProtectionError(
            f"Unsupported URL scheme '{parsed.scheme}'. Only HTTP and HTTPS are permitted."
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFProtectionError("URL does not contain a valid hostname.")

    hostname_lower = hostname.lower()

    # 2. Obvious loopback checks
    if hostname_lower in ("localhost", "localhost.localdomain") or hostname_lower.endswith(".localhost"):
        raise SSRFProtectionError(f"Target host '{hostname}' is a loopback address and is blocked.")

    # 3. Quick prefix checks for link-local and loopback IPs
    if hostname_lower.startswith("169.254."):
        raise SSRFProtectionError(f"Target host '{hostname}' is a link-local address and is blocked.")
    if hostname_lower.startswith("127."):
        raise SSRFProtectionError(f"Target host '{hostname}' is a loopback address and is blocked.")

    # 4. Resolve hostname via DNS and check every resolved IP
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        # If host cannot be resolved, reject or raise
        raise SSRFProtectionError(f"Unable to resolve host '{hostname}': {e}")

    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        if ip.is_loopback:
            raise SSRFProtectionError(f"Host '{hostname}' resolves to loopback IP {ip_str}, which is blocked.")
        if ip.is_link_local:
            raise SSRFProtectionError(f"Host '{hostname}' resolves to link-local IP {ip_str}, which is blocked.")
        if ip.is_private:
            raise SSRFProtectionError(f"Host '{hostname}' resolves to private network IP {ip_str}, which is blocked.")
        if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise SSRFProtectionError(f"Host '{hostname}' resolves to non-routable/reserved IP {ip_str}, which is blocked.")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that validates target URLs against SSRF policies."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_video_from_url(
    url: str,
    dest_path: Path,
    max_bytes: int = MAX_VIDEO_DOWNLOAD_BYTES,
    timeout_s: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    _mock_content: bytes | None = None,
    _mock_redirect: str | None = None,
) -> Path:
    """
    Download a video file from a validated URL with SSRF protection, size limits, and timeouts.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate target URL first
    validate_url_target(url)

    # Handle mock redirect test branch
    if _mock_redirect:
        validate_url_target(_mock_redirect)
        url = _mock_redirect

    # Handle mock content test branch (for offline tests)
    if _mock_content is not None:
        if len(_mock_content) > max_bytes:
            raise AppError(
                code="PAYLOAD_TOO_LARGE",
                message=f"Downloaded content exceeds maximum limit of {max_bytes} bytes.",
                status_code=413,
                retryable=False,
            )
        dest_path.write_bytes(_mock_content)
        return dest_path

    # Live network download using SafeRedirectHandler
    opener = urllib.request.build_opener(SafeRedirectHandler())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PromptCut/1.0 (Integration Standard)"},
    )

    tmp_dest = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        with opener.open(request, timeout=timeout_s) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    cl_int = int(content_length)
                    if cl_int > max_bytes:
                        raise AppError(
                            code="PAYLOAD_TOO_LARGE",
                            message=f"Video file length ({cl_int} bytes) exceeds limit ({max_bytes} bytes).",
                            status_code=413,
                            retryable=False,
                        )
                except ValueError:
                    pass

            bytes_written = 0
            with open(tmp_dest, "wb") as f_out:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        raise AppError(
                            code="PAYLOAD_TOO_LARGE",
                            message=f"Downloaded video stream exceeds maximum limit of {max_bytes} bytes.",
                            status_code=413,
                            retryable=False,
                        )
                    f_out.write(chunk)

        tmp_dest.replace(dest_path)
        return dest_path
    finally:
        if tmp_dest.is_file():
            tmp_dest.unlink(missing_ok=True)
