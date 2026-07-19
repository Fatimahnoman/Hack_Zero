import json
from pathlib import Path
from threading import RLock
from typing import Any, Optional
from datetime import datetime

from golden_tier_external_world.config.enums import PlatformType, EventType
from golden_tier_external_world.events.models import BaseEvent
from golden_tier_external_world.storage.interface import StorageInterface


def _platform_key(p: PlatformType | str) -> str:
    return p.value if isinstance(p, PlatformType) else p


class FileStorage(StorageInterface):
    def __init__(self, vault_path: Path) -> None:
        self._root = vault_path
        self._lock = RLock()
        self._events_dir = vault_path / "events"
        self._processed_path = vault_path / "processed.json"
        self._state_path = vault_path / "state.json"
        self._poll_path = vault_path / "poll_times.json"

        self._events_dir.mkdir(parents=True, exist_ok=True)

        self._processed: dict[str, str] = {}
        self._state: dict[str, Any] = {}
        self._poll_times: dict[str, str] = {}

        self._load_all()

    def _load_all(self) -> None:
        with self._lock:
            if self._processed_path.exists():
                try:
                    self._processed = json.loads(self._processed_path.read_text(encoding="utf-8")) or {}
                except (json.JSONDecodeError, OSError):
                    self._processed = {}
            if self._state_path.exists():
                try:
                    self._state = json.loads(self._state_path.read_text(encoding="utf-8")) or {}
                except (json.JSONDecodeError, OSError):
                    self._state = {}
            if self._poll_path.exists():
                try:
                    self._poll_times = json.loads(self._poll_path.read_text(encoding="utf-8")) or {}
                except (json.JSONDecodeError, OSError):
                    self._poll_times = {}

    def save_event(self, event: BaseEvent) -> None:
        with self._lock:
            platform_dir = self._events_dir / event.platform.value
            platform_dir.mkdir(parents=True, exist_ok=True)
            event_file = platform_dir / f"{event.event_type.name.lower()}.jsonl"
            data = {
                "id": event.event_id,
                "type": event.event_type.name,
                "platform": event.platform.value,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "raw": event.raw_data,
            }
            with open(event_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, default=str) + "\n")

    def get_events(
        self,
        platform: Optional[PlatformType] = None,
        event_type: Optional[EventType] = None,
        limit: int = 100,
    ) -> list[BaseEvent]:
        return []

    def is_processed(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._processed

    def mark_processed(self, event_id: str) -> None:
        with self._lock:
            self._processed[event_id] = datetime.now().isoformat()
            self._processed_path.write_text(
                json.dumps(self._processed, indent=2, default=str),
                encoding="utf-8",
            )

    def get_last_poll_time(self, platform: PlatformType) -> Optional[str]:
        with self._lock:
            return self._poll_times.get(_platform_key(platform))

    def set_last_poll_time(self, platform: PlatformType, timestamp: str) -> None:
        with self._lock:
            self._poll_times[_platform_key(platform)] = timestamp
            self._poll_path.write_text(
                json.dumps(self._poll_times, indent=2, default=str),
                encoding="utf-8",
            )

    def save_state(self, platform: PlatformType, key: str, value: object) -> None:
        with self._lock:
            k = f"{_platform_key(platform)}:{key}"
            self._state[k] = value
            self._state_path.write_text(
                json.dumps(self._state, indent=2, default=str),
                encoding="utf-8",
            )

    def load_state(self, platform: PlatformType, key: str) -> Optional[object]:
        with self._lock:
            k = f"{_platform_key(platform)}:{key}"
            return self._state.get(k)
