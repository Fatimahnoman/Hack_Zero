from typing import Any, Optional
from threading import RLock

from golden_tier_external_world.storage.backends.base import StorageBackend


class StateVault:
    def __init__(self, backend: StorageBackend, auto_save: bool = True) -> None:
        self._backend = backend
        self._auto_save = auto_save
        self._lock = RLock()
        self._data: dict[str, Any] = {}
        self._dirty: bool = False

        self.load()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._dirty = True
            if self._auto_save:
                self.save()

    def set_batch(self, mapping: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(mapping)
            self._dirty = True
            if self._auto_save:
                self.save()

    def remove(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                del self._data[key]
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

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def load(self) -> None:
        with self._lock:
            loaded = self._backend.read()
            self._data = {}
            for key, value in loaded.items():
                self._data[str(key)] = value
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

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            if key not in self._data:
                raise KeyError(key)
            return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __contains__(self, key: str) -> bool:
        return self.exists(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __repr__(self) -> str:
        with self._lock:
            return f"StateVault({len(self._data)} keys)"
