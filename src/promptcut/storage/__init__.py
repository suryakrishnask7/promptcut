"""
src/promptcut/storage/__init__.py

IncuBrix Integration Standard #9:
Storage module providing abstract interface with Local and S3/MinIO implementations.
Backend selected by environment variable: STORAGE_BACKEND (default: local).
"""

from __future__ import annotations

import os
from promptcut.storage.base import StorageAdapter
from promptcut.storage.local import LocalStorageAdapter
from promptcut.storage.s3 import S3StorageAdapter


def get_storage_adapter() -> StorageAdapter:
    """Factory selecting the storage adapter based on STORAGE_BACKEND env var."""
    backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    if backend in ("s3", "minio"):
        return S3StorageAdapter()
    return LocalStorageAdapter()


__all__ = [
    "StorageAdapter",
    "LocalStorageAdapter",
    "S3StorageAdapter",
    "get_storage_adapter",
]
