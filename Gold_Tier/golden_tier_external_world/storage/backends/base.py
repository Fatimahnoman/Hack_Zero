from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    @abstractmethod
    def read(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def write(self, data: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def clear(self) -> None:
        ...
