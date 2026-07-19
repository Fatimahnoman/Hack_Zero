from typing import Any, Callable, Optional


class DIContainer:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Any]] = {}
        self._singletons: dict[str, Any] = {}
        self._singleton_flags: dict[str, bool] = {}

    def register(
        self,
        key: str,
        factory: Callable[[], Any],
        singleton: bool = True,
    ) -> None:
        self._factories[key] = factory
        self._singleton_flags[key] = singleton
        if key in self._singletons:
            del self._singletons[key]

    def register_instance(self, key: str, instance: Any) -> None:
        self._singletons[key] = instance
        self._singleton_flags[key] = True

    def resolve(self, key: str) -> Any:
        if key in self._singletons:
            return self._singletons[key]

        factory = self._factories.get(key)
        if factory is None:
            raise KeyError(f"No registration found for: {key}")

        instance = factory()

        if self._singleton_flags.get(key, True):
            self._singletons[key] = instance

        return instance

    def has(self, key: str) -> bool:
        return key in self._factories or key in self._singletons

    def clear(self) -> None:
        self._factories.clear()
        self._singletons.clear()
        self._singleton_flags.clear()


container = DIContainer()
