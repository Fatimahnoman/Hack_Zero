from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from threading import RLock
from typing import Any, Callable, Optional
import logging


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        success_threshold: int = 2,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._half_open_calls = 0
        self._lock = RLock()
        self._logger = logging.getLogger(f"CircuitBreaker.{name}")

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if not self._try_acquire():
            raise CircuitOpenError(
                f"Circuit breaker '{self._name}' is {self._state.value}"
            )

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _try_acquire(self) -> bool:
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if self._should_half_open():
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 1
                    self._success_count = 0
                    self._logger.info(
                        "Circuit transitioning OPEN -> HALF_OPEN | name=%s",
                        self._name,
                    )
                    return True
                return False

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self._half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            return False

    def _should_half_open(self) -> bool:
        if self._last_failure_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_failure_time).total_seconds()
        return elapsed >= self._recovery_timeout

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._half_open_calls = 0
                    self._logger.info(
                        "Circuit reset to CLOSED | name=%s", self._name,
                    )

    def _on_failure(self) -> None:
        with self._lock:
            self._last_failure_time = datetime.now(timezone.utc)
            self._failure_count += 1

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._logger.warning(
                    "Circuit HALF_OPEN -> OPEN | name=%s | failures=%d",
                    self._name, self._failure_count,
                )
                return

            if self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._logger.warning(
                    "Circuit CLOSED -> OPEN | name=%s | failures=%d/%d",
                    self._name, self._failure_count, self._failure_threshold,
                )

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
            self._last_failure_time = None
            self._logger.info("Circuit manually reset | name=%s", self._name)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self._failure_threshold,
                "success_count": self._success_count,
                "success_threshold": self._success_threshold,
                "half_open_calls": self._half_open_calls,
                "last_failure_time": (
                    self._last_failure_time.isoformat()
                    if self._last_failure_time else None
                ),
            }
