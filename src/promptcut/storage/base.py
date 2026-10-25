"""
src/promptcut/storage/base.py

IncuBrix Integration Standard #9:
Abstract StorageAdapter interface defining cloud and local media/session storage operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageAdapter(ABC):
    """Abstract interface for storing and retrieving session artifacts and media files."""

    @abstractmethod
    def save_file(self, key: str, data: bytes | Path, content_type: str | None = None) -> str:
        """Store a file or raw bytes at key. Returns stored URI or absolute local path."""
        pass

    @abstractmethod
    def get_file(self, key: str) -> bytes:
        """Retrieve raw file bytes for a given key."""
        pass

    @abstractmethod
    def file_exists(self, key: str) -> bool:
        """Check if an object exists at the given key."""
        pass

    @abstractmethod
    def delete_file(self, key: str) -> None:
        """Remove a file at the given key."""
        pass

    @abstractmethod
    def get_url(self, key: str) -> str:
        """Return a public, signed, or relative URL for accessing the resource."""
        pass

    @abstractmethod
    def is_ready(self) -> bool:
        """Check whether the storage backend is reachable and writable."""
        pass
