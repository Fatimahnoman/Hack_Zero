from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Union
from pathlib import Path
import logging
import time
import random

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.events.bus import EventBus
from golden_tier_external_world.browser import PlaywrightManager


@dataclass
class PostContent:
    text: str
    media_paths: list[Union[str, Path]] = field(default_factory=list)
    visibility: str = "public"

    def __post_init__(self) -> None:
        self.media_paths = [Path(p) for p in self.media_paths]


class PosterError(Exception):
    def __init__(self, message: str, platform: Optional[str] = None) -> None:
        self.platform = platform
        prefix = f"[{platform}] " if platform else ""
        super().__init__(f"{prefix}{message}")


class BasePoster(ABC):
    def __init__(
        self,
        storage: StorageInterface,
        event_bus: EventBus,
        browser_manager: Optional[PlaywrightManager] = None,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        backoff_jitter: float = 0.5,
        headless: bool = True,
        screenshot_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._backoff_jitter = backoff_jitter
        self._headless = headless
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None

        self._browser: PlaywrightManager = browser_manager or PlaywrightManager(
            headless=headless,
            screenshot_dir=self._screenshot_dir,
        )
        self._authenticated = False
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def platform(self) -> PlatformType:
        ...

    @abstractmethod
    def authenticate(self) -> bool:
        ...

    @abstractmethod
    def post(self, content: PostContent) -> str:
        ...

    def retry(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        last_exception: Optional[Exception] = None
        attempts = self._max_retries

        for attempt in range(1, attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_exception = e
                self._logger.warning(
                    "Attempt %d/%d failed | platform=%s | error=%s",
                    attempt,
                    attempts,
                    self.platform.value,
                    e,
                )
                if self._screenshot_dir:
                    self._browser.screenshot(name=f"retry_{attempt}")
                if attempt < attempts:
                    delay = self._backoff_delay(attempt)
                    self._logger.info(
                        "Retrying in %.1fs | attempt=%d",
                        delay,
                        attempt + 1,
                    )
                    time.sleep(delay)

        raise PosterError(
            message=f"Operation failed after {attempts} attempts",
            platform=self.platform.value,
        ) from last_exception

    def _backoff_delay(self, attempt: int) -> float:
        delay = min(self._backoff_max, self._backoff_base ** attempt)
        jitter = random.uniform(0, self._backoff_jitter)
        return delay + jitter

    def _ensure_browser(self) -> None:
        if not self._browser.is_running:
            self._browser.start()
        self._browser.ensure_alive()

    def _new_page(self) -> Any:
        self._ensure_browser()
        return self._browser.new_page()
