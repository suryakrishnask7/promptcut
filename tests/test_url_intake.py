"""
tests/test_url_intake.py

Verifies IncuBrix Integration Standard #5:
- Video input by URL in addition to upload
- http/https only
- Block private/loopback/link-local IPs (also after redirects)
- Size and time limits
- Tests that localhost and 169.254.x are rejected
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from promptcut.api.app import create_app
from promptcut.api.errors import AppError
from promptcut.media.url_intake import (
    validate_url_target,
    download_video_from_url,
    SSRFProtectionError,
    MAX_VIDEO_DOWNLOAD_BYTES,
)


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_reject_unsupported_schemes():
    """file://, ftp://, gopher:// schemes must be rejected."""
    with pytest.raises(SSRFProtectionError) as exc_info:
        validate_url_target("file:///etc/passwd")
    assert "scheme" in str(exc_info.value).lower()

    with pytest.raises(SSRFProtectionError) as exc_info:
        validate_url_target("ftp://example.com/video.mp4")
    assert "scheme" in str(exc_info.value).lower()


def test_reject_localhost():
    """localhost and 127.0.0.1 must be rejected."""
    with pytest.raises(SSRFProtectionError) as exc_info:
        validate_url_target("http://localhost/video.mp4")
    assert "loopback" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()

    with pytest.raises(SSRFProtectionError) as exc_info:
        validate_url_target("http://127.0.0.1:8000/video.mp4")
    assert "loopback" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()


def test_reject_link_local_and_cloud_metadata():
    """169.254.x.x link-local and AWS/GCP metadata endpoints must be rejected."""
    with pytest.raises(SSRFProtectionError) as exc_info:
        validate_url_target("http://169.254.169.254/latest/meta-data")
    assert "link-local" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()

    with pytest.raises(SSRFProtectionError) as exc_info:
        validate_url_target("http://169.254.1.1/video.mp4")
    assert "link-local" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()


def test_reject_private_networks():
    """RFC 1918 private subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) must be rejected."""
    for url in [
        "http://10.0.0.1/video.mp4",
        "http://172.16.0.1/video.mp4",
        "http://192.168.1.50/video.mp4",
    ]:
        with pytest.raises(SSRFProtectionError) as exc_info:
            validate_url_target(url)
        assert "private" in str(exc_info.value).lower() or "blocked" in str(exc_info.value).lower()


def test_redirect_to_private_ip_is_blocked(tmp_path):
    """If a valid URL redirects to a private IP (e.g. 127.0.0.1), download must abort."""
    dest = tmp_path / "downloaded.mp4"

    # Simulate redirecting response to 127.0.0.1
    redirect_target = "http://127.0.0.1:8080/secret.mp4"
    with pytest.raises(SSRFProtectionError):
        download_video_from_url(
            "https://example.com/initial-redirect",
            dest,
            _mock_redirect=redirect_target,
        )


def test_download_size_limit_exceeded(tmp_path):
    """Downloads exceeding size limit must raise an error."""
    dest = tmp_path / "toolarge.mp4"
    with pytest.raises(AppError) as exc_info:
        download_video_from_url(
            "https://example.com/huge.mp4",
            dest,
            max_bytes=100,
            _mock_content=b"x" * 200,
        )
    assert exc_info.value.code == "PAYLOAD_TOO_LARGE"


def test_successful_public_download(tmp_path):
    """Valid public URL downloads video successfully."""
    dest = tmp_path / "valid.mp4"
    dummy_video = b"ID3\x00\x00synthetic_video_bytes"

    # Mock DNS resolution to public IP 93.184.216.34 (example.com)
    download_video_from_url(
        "https://example.com/sample.mp4",
        dest,
        _mock_content=dummy_video,
    )
    assert dest.is_file()
    assert dest.read_bytes() == dummy_video


def test_api_session_creation_rejects_localhost_url(client: TestClient):
    """POST /api/v1/sessions with video_url pointing to localhost returns 400 SSRF_BLOCKED."""
    res = client.post(
        "/api/v1/sessions",
        json={"video_url": "http://localhost:8000/private-clip.mp4"},
    )
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "SSRF_BLOCKED"
    assert err["retryable"] is False


def test_api_session_creation_rejects_link_local_url(client: TestClient):
    """POST /api/v1/sessions with video_url pointing to 169.254.x returns 400 SSRF_BLOCKED."""
    res = client.post(
        "/api/v1/sessions",
        json={"video_url": "http://169.254.169.254/latest/meta-data"},
    )
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "SSRF_BLOCKED"
    assert err["retryable"] is False
