"""
src/promptcut/storage/local.py

IncuBrix Integration Standard #9:
LocalStorageAdapter storing session files, media, and manifests on the local filesystem.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from promptcut.config import SESSIONS_DIR
from promptcut.storage.base import StorageAdapter


class LocalStorageAdapter(StorageAdapter):
    """Local filesystem implementation of StorageAdapter."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir).resolve() if base_dir else SESSIONS_DIR.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        # Prevent directory traversal attacks
        clean_key = Path(key.lstrip("/\\"))
        target = (self.base_dir / clean_key).resolve()
        if not str(target).startswith(str(self.base_dir)):
            raise ValueError(f"Path traversal detected: {key}")
        return target

    def save_file(self, key: str, data: bytes | Path, content_type: str | None = None) -> str:
        target_path = self._resolve_path(key)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, Path):
            shutil.copyfile(data, target_path)
        else:
            target_path.write_bytes(data)

        return str(target_path)

    def get_file(self, key: str) -> bytes:
        target_path = self._resolve_path(key)
        if not target_path.is_file():
            raise FileNotFoundError(f"File not found in local storage: {key}")
        return target_path.read_bytes()

    def file_exists(self, key: str) -> bool:
        target_path = self._resolve_path(key)
        return target_path.is_file()

    def delete_file(self, key: str) -> None:
        target_path = self._resolve_path(key)
        if target_path.is_file():
            target_path.unlink()

    def get_url(self, key: str) -> str:
        clean_key = key.lstrip("/\\").replace("\\", "/")
        return f"/api/v1/storage/{clean_key}"

    def is_ready(self) -> bool:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            probe = self.base_dir / f".ready_probe_{uuid.uuid4().hex[:6]}"
            probe.write_text("probe")
            probe.unlink(missing_ok=True)
            return True
        except Exception:
            return False
