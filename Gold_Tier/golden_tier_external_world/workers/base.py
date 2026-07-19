from abc import ABC, abstractmethod
from typing import Any
import logging

from golden_tier_external_world.events.models import BaseEvent
from golden_tier_external_world.events.bus import EventBus
from golden_tier_external_world.storage.interface import StorageInterface


class BaseWorker(ABC):
    def __init__(
        self,
        storage: StorageInterface,
        event_bus: EventBus,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._logger = logging.getLogger(f"{self.__class__.__name__}")

    @abstractmethod
    def process(self, event: BaseEvent) -> Any:
        ...

    def can_handle(self, event: BaseEvent) -> bool:
        return True

    def register(self) -> None:
        self._event_bus.subscribe(self.event_type, self.process)

    @property
    @abstractmethod
    def event_type(self) -> "EventType":  # type: ignore[name-defined]
        ...
