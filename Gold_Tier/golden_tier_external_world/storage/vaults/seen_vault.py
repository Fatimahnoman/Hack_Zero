from datetime import datetime, timezone
from typing import Any, Optional
from threading import RLock

from golden_tier_external_world.storage.backends.base import StorageBackend


class SeenVault:
    def __init__(self, backend: StorageBackend, auto_save: bool = True) -> None:
        self._backend = backend
        self._auto_save = auto_save
        self._lock = RLock()
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty: bool = False

        self.load()

    def is_seen(self, item_id: str) -> bool:
        with self._lock:
            return item_id in self._data

    def mark_seen(
        self,
        item_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        with self._lock:
            if item_id in self._data:
                return False
            entry: dict[str, Any] = {
                "first_seen": datetime.now(timezone.utc).isoformat(),
            }
            if metadata:
                entry["metadata"] = metadata
            self._data[item_id] = entry
            self._dirty = True
            if self._auto_save:
                self.save()
            return True

    def mark_seen_batch(self, item_ids: list[str]) -> int:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            count = 0
            for item_id in item_ids:
                if item_id not in self._data:
                    self._data[item_id] = {"first_seen": now}
                    count += 1
            if count > 0:
                self._dirty = True
                if self._auto_save:
                    self.save()
            return count

    def remove(self, item_id: str) -> bool:
        with self._lock:
            if item_id in self._data:
                del self._data[item_id]
                self._dirty = True
                if self._auto_save:
                    self.save()
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._dirty = True
            self._backend.clear()

    def get_metadata(self, item_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._data.get(item_id)
            if entry is None:
                return None
            return entry.get("metadata")

    def count(self) -> int:
        with self._lock:
            return len(self._data)

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    def load(self) -> None:
        with self._lock:
            loaded = self._backend.read()
            self._data = {}
            for key, value in loaded.items():
                if isinstance(value, dict):
                    self._data[str(key)] = value
                else:
                    self._data[str(key)] = {"first_seen": str(value)}
            self._dirty = False

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._backend.write(self._data)
            self._dirty = False

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, item_id: str) -> bool:
        return self.is_seen(item_id)
