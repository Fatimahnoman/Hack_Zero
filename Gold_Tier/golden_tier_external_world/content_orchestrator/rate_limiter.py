from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Optional
import logging
import time

from golden_tier_external_world.config.enums import PlatformType


_DEFAULT_LIMITS: dict[PlatformType, tuple[int, float]] = {
    PlatformType.FACEBOOK: (200, 86400.0),
    PlatformType.TWITTER: (300, 86400.0),
    PlatformType.INSTAGRAM: (100, 86400.0),
    PlatformType.LINKEDIN: (100, 86400.0),
}


class RateLimiter:
    def __init__(
        self,
        limits: Optional[dict[PlatformType, tuple[int, float]]] = None,
    ) -> None:
        self._limits: dict[PlatformType, tuple[int, float]] = limits or dict(_DEFAULT_LIMITS)
        self._windows: dict[PlatformType, list[float]] = defaultdict(list)
        self._lock = RLock()
        self._logger = logging.getLogger(self.__class__.__name__)

    def set_limit(self, platform: PlatformType, max_calls: int, window_seconds: float) -> None:
        self._limits[platform] = (max_calls, window_seconds)

    def allow(self, platform: PlatformType) -> bool:
        with self._lock:
            now = time.time()
            max_calls, window = self._limits.get(platform, (0, 1.0))
            if max_calls <= 0:
                return False

            window_start = now - window
            self._windows[platform] = [
                ts for ts in self._windows[platform] if ts > window_start
            ]

            if len(self._windows[platform]) >= max_calls:
                self._logger.warning(
                    "Rate limit exceeded | platform=%s | current=%d | max=%d | window=%.0fs",
                    platform.value,
                    len(self._windows[platform]),
                    max_calls,
                    window,
                )
                return False

            self._windows[platform].append(now)
            return True

    def consume(self, platform: PlatformType) -> bool:
        return self.allow(platform)

    def remaining(self, platform: PlatformType) -> int:
        with self._lock:
            now = time.time()
            max_calls, window = self._limits.get(platform, (0, 1.0))
            window_start = now - window
            self._windows[platform] = [
                ts for ts in self._windows[platform] if ts > window_start
            ]
            return max(0, max_calls - len(self._windows[platform]))

    def reset(self, platform: Optional[PlatformType] = None) -> None:
        with self._lock:
            if platform:
                self._windows[platform].clear()
                self._logger.info("Rate limit reset | platform=%s", platform.value)
            else:
                for p in self._windows:
                    self._windows[p].clear()
                self._logger.info("Rate limit reset for all platforms")

    def limits(self) -> dict[PlatformType, dict]:
        with self._lock:
            result: dict[PlatformType, dict] = {}
            for platform in self._limits:
                max_calls, window = self._limits[platform]
                result[platform] = {
                    "max_calls": max_calls,
                    "window_seconds": window,
                    "current_count": len(self._windows.get(platform, [])),
                    "remaining": self.remaining(platform),
                }
            return result
