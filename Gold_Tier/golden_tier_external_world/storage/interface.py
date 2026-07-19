from abc import ABC, abstractmethod
from typing import Optional

from golden_tier_external_world.config.enums import PlatformType, EventType
from golden_tier_external_world.events.models import BaseEvent


class StorageInterface(ABC):
    @abstractmethod
    def save_event(self, event: BaseEvent) -> None: ...

    @abstractmethod
    def get_events(
        self,
        platform: Optional[PlatformType] = None,
        event_type: Optional[EventType] = None,
        limit: int = 100,
    ) -> list[BaseEvent]: ...

    @abstractmethod
    def is_processed(self, event_id: str) -> bool: ...

    @abstractmethod
    def mark_processed(self, event_id: str) -> None: ...

    @abstractmethod
    def get_last_poll_time(self, platform: PlatformType) -> Optional[str]: ...

    @abstractmethod
    def set_last_poll_time(self, platform: PlatformType, timestamp: str) -> None: ...

    @abstractmethod
    def save_state(self, platform: PlatformType, key: str, value: object) -> None: ...

    @abstractmethod
    def load_state(self, platform: PlatformType, key: str) -> Optional[object]: ...
