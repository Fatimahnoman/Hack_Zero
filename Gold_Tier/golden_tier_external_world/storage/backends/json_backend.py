import json
import os
import tempfile
from pathlib import Path
from typing import Any
from threading import RLock

from golden_tier_external_world.storage.backends.base import StorageBackend


class JsonBackend(StorageBackend):
    def __init__(self, file_path: str | Path, auto_create: bool = True) -> None:
        self._path = Path(file_path)
        self._lock = RLock()

        if auto_create:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return {}
            try:
                data = self._path.read_text(encoding="utf-8")
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    return parsed
                return {}
            except (json.JSONDecodeError, OSError):
                return {}

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._atomic_write(data)

    def clear(self) -> None:
        with self._lock:
            self._path.unlink(missing_ok=True)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f".{self._path.stem}.",
            dir=self._path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(self._path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @property
    def path(self) -> Path:
        return self._path
