"""
tests/test_storage.py

Verifies IncuBrix Integration Standard #9:
- Storage interface with Local implementation and S3/MinIO implementation behind it
- Selected by environment variable (STORAGE_BACKEND=local|s3|minio)
- CRUD operations: save_file, get_file, file_exists, delete_file, get_url
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from promptcut.storage import (
    StorageAdapter,
    LocalStorageAdapter,
    S3StorageAdapter,
    get_storage_adapter,
)


def test_local_storage_adapter(tmp_path: Path):
    """LocalStorageAdapter saves, retrieves, checks existence, and deletes files."""
    adapter = LocalStorageAdapter(base_dir=tmp_path)

    key = "sessions/test1/video.mp4"
    test_data = b"synthetic video bytes 12345"

    # 1. Save
    saved_path = adapter.save_file(key, test_data)
    assert Path(saved_path).is_file()

    # 2. Exists
    assert adapter.file_exists(key) is True
    assert adapter.file_exists("nonexistent/key.mp4") is False

    # 3. Retrieve
    retrieved = adapter.get_file(key)
    assert retrieved == test_data

    # 4. URL
    url = adapter.get_url(key)
    assert "sessions/test1/video.mp4" in url

    # 5. Delete
    adapter.delete_file(key)
    assert adapter.file_exists(key) is False


def test_s3_storage_adapter_mocked():
    """S3StorageAdapter interacts with S3/MinIO client correctly."""
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=b"mock s3 data"))
    }
    mock_s3.head_object.return_value = {"ContentLength": 12}

    adapter = S3StorageAdapter(
        bucket_name="promptcut-media",
        endpoint_url="http://localhost:9000",
        s3_client=mock_s3,
    )

    # Save
    adapter.save_file("renders/rendered.mp4", b"mock s3 data", content_type="video/mp4")
    mock_s3.put_object.assert_called_once_with(
        Bucket="promptcut-media",
        Key="renders/rendered.mp4",
        Body=b"mock s3 data",
        ContentType="video/mp4",
    )

    # Exists
    assert adapter.file_exists("renders/rendered.mp4") is True
    mock_s3.head_object.assert_called_once_with(
        Bucket="promptcut-media",
        Key="renders/rendered.mp4",
    )

    # Retrieve
    content = adapter.get_file("renders/rendered.mp4")
    assert content == b"mock s3 data"

    # Delete
    adapter.delete_file("renders/rendered.mp4")
    mock_s3.delete_object.assert_called_once_with(
        Bucket="promptcut-media",
        Key="renders/rendered.mp4",
    )


def test_storage_adapter_factory(monkeypatch, tmp_path):
    """get_storage_adapter selects backend based on STORAGE_BACKEND env var."""
    # Default is local
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    adapter = get_storage_adapter()
    assert isinstance(adapter, LocalStorageAdapter)

    # Explicit local
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    adapter = get_storage_adapter()
    assert isinstance(adapter, LocalStorageAdapter)

    # S3 / MinIO backend selection
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    with patch("promptcut.storage.s3.create_s3_client") as mock_create:
        mock_create.return_value = MagicMock()
        adapter = get_storage_adapter()
        assert isinstance(adapter, S3StorageAdapter)
