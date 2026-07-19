from abc import ABC, abstractmethod
from typing import Optional, Callable, Any
from datetime import datetime, timezone
from threading import Event
import logging
import time
import random
import signal
import os

from golden_tier_external_world.config.enums import PlatformType, WatcherState
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.events.models import BaseEvent
from golden_tier_external_world.events.bus import EventBus
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.exceptions import (
    WatcherError,
    RetryExhaustedError,
    HealthCheckError,
    AuthenticationError,
    PollingError,
)

T = Any


class BaseWatcher(ABC):
    def __init__(
        self,
        config: WatcherConfig,
        storage: StorageInterface,
        event_bus: EventBus,
    ) -> None:
        self._config = config
        self._storage = storage
        self._event_bus = event_bus
        self._state: WatcherState = WatcherState.IDLE
        self._logger = logging.getLogger(f"{self.__class__.__name__}")
        self._stop_event = Event()

        self._poll_count: int = 0
        self._error_count: int = 0
        self._consecutive_failures: int = 0
        self._last_poll_time: Optional[datetime] = None
        self._last_heartbeat_time: Optional[datetime] = None
        self._last_health_check_time: Optional[datetime] = None
        self._start_time: Optional[datetime] = None

        self._load_state()

    @property
    def platform(self) -> PlatformType:
        return self._config.platform

    @property
    def state(self) -> WatcherState:
        return self._state

    @property
    def config(self) -> WatcherConfig:
        return self._config

    @property
    def uptime(self) -> Optional[float]:
        if self._start_time is None:
            return None
        return (datetime.now(timezone.utc) - self._start_time).total_seconds()

    @abstractmethod
    def authenticate(self) -> bool:
        ...

    @abstractmethod
    def poll(self) -> list[BaseEvent]:
        ...

    def start(self) -> None:
        self._start_time = datetime.now(timezone.utc)
        self._state = WatcherState.RUNNING
        self._stop_event.clear()
        self._logger.info(
            "Watcher started | platform=%s | interval=%ds | retries=%d",
            self.platform.value,
            self._config.poll_interval_seconds,
            self._config.max_retries,
        )

        self._setup_signal_handlers()

        try:
            if not self.authenticate():
                self._state = WatcherState.ERROR
                self._logger.error("Authentication failed for %s", self.platform.value)
                return

            self._logger.info("Authentication successful for %s", self.platform.value)
            self._run_main_loop()

        except KeyboardInterrupt:
            self._logger.info("KeyboardInterrupt received")
        except Exception:
            self._logger.exception("Fatal error in watcher main loop")
            self._state = WatcherState.ERROR
        finally:
            self.graceful_shutdown()

    def _run_main_loop(self) -> None:
        while self._should_continue():
            if self._state == WatcherState.PAUSED:
                self._interruptible_sleep(self._config.sleep_granularity)
                continue

            try:
                if not self.health_check():
                    self._interruptible_sleep(self._config.poll_interval_seconds)
                    continue

                self._run_poll_cycle()
                self._consecutive_failures = 0

                if self._poll_count % self._config.heartbeat_interval_polls == 0:
                    self.heartbeat()

            except AuthenticationError:
                self._state = WatcherState.ERROR
                self._logger.error("Authentication lost, stopping watcher")
                break

            except RetryExhaustedError as e:
                self._error_count += 1
                self._consecutive_failures += 1
                self._logger.error("Poll failed after %d retries: %s", e.attempts, e)

            except PollingError as e:
                self._error_count += 1
                self._consecutive_failures += 1
                self._logger.warning("Polling error (recoverable): %s", e)

            except Exception:
                self._error_count += 1
                self._consecutive_failures += 1
                self._logger.exception("Unhandled error in poll cycle")

            self._save_state()
            self._interruptible_sleep(self._config.poll_interval_seconds)

    def _run_poll_cycle(self) -> None:
        events = self._execute_poll()
        for event in events:
            try:
                self.emit_event(event)
            except Exception:
                self._logger.exception("Failed to emit event %s", event.event_id)

        self._poll_count += 1
        self._last_poll_time = datetime.now(timezone.utc)
        self._logger.debug(
            "Poll cycle complete | poll=%d | events=%d",
            self._poll_count,
            len(events),
        )

    def _execute_poll(self) -> list[BaseEvent]:
        if self._config.max_retries > 0:
            return self.retry(
                self._poll_with_logging,
                max_attempts=self._config.max_retries,
            )
        return self._poll_with_logging()

    def _poll_with_logging(self) -> list[BaseEvent]:
        try:
            events = self.poll()
            return events or []
        except Exception:
            self._logger.debug("Poll attempt failed")
            raise

    def emit_event(self, event: BaseEvent) -> None:
        self._storage.save_event(event)
        self._event_bus.publish(event)
        self._storage.mark_processed(event.event_id)
        self._logger.info(
            "Event emitted | id=%s | type=%s | platform=%s",
            event.event_id,
            event.event_type.name if hasattr(event, "event_type") else "?",
            event.platform.value,
        )

    def stop(self) -> None:
        self._logger.info(
            "Stop requested | platform=%s | poll_count=%d",
            self.platform.value,
            self._poll_count,
        )
        self._state = WatcherState.STOPPED
        self._stop_event.set()

    def pause(self) -> None:
        if self._state == WatcherState.RUNNING:
            self._state = WatcherState.PAUSED
            self._logger.info("Watcher paused | platform=%s", self.platform.value)

    def resume(self) -> None:
        if self._state == WatcherState.PAUSED:
            self._state = WatcherState.RUNNING
            self._logger.info("Watcher resumed | platform=%s", self.platform.value)

    def health_check(self) -> bool:
        now = datetime.now(timezone.utc)
        self._last_health_check_time = now

        if self._state == WatcherState.ERROR:
            self._logger.warning("Health: ERROR state")
            return False

        if self._consecutive_failures > 10:
            self._logger.warning(
                "Health: too many consecutive failures (%d)",
                self._consecutive_failures,
            )
            return False

        return True

    def retry(
        self,
        fn: Callable[..., T],
        *args: Any,
        max_attempts: Optional[int] = None,
        **kwargs: Any,
    ) -> T:
        attempts = max_attempts or self._config.max_retries
        last_exception: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                return fn(*args, **kwargs)
            except AuthenticationError:
                raise
            except Exception as e:
                last_exception = e
                self._logger.warning(
                    "Retry attempt %d/%d failed | platform=%s | error=%s",
                    attempt,
                    attempts,
                    self.platform.value,
                    e,
                )

                if attempt < attempts:
                    delay = self._backoff_delay(attempt)
                    self._interruptible_sleep(delay)

        raise RetryExhaustedError(
            message=f"Operation failed for {self.platform.value}",
            platform=self.platform.value,
            attempts=attempts,
            last_exception=last_exception,
        )

    def _backoff_delay(self, attempt: int) -> float:
        delay = min(
            self._config.backoff_max,
            self._config.backoff_base ** attempt,
        )
        jitter = random.uniform(0, self._config.backoff_jitter)
        return delay + jitter

    def _interruptible_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            remaining = deadline - time.monotonic()
            chunk = min(self._config.sleep_granularity, max(0.05, remaining))
            time.sleep(chunk)

    def heartbeat(self) -> None:
        self._last_heartbeat_time = datetime.now(timezone.utc)
        self._logger.info(
            "Heartbeat | platform=%s | polls=%d | errors=%d | state=%s | uptime=%.1fs",
            self.platform.value,
            self._poll_count,
            self._error_count,
            self._state.value,
            self.uptime or 0,
        )
        self._save_state()

    def graceful_shutdown(self) -> None:
        self._logger.info(
            "Graceful shutdown | platform=%s | polls=%d | errors=%d",
            self.platform.value,
            self._poll_count,
            self._error_count,
        )
        self._state = WatcherState.STOPPED
        self._stop_event.set()
        self._save_state()
        self._logger.info("Shutdown complete | platform=%s", self.platform.value)

    def _should_continue(self) -> bool:
        return (
            self._state not in (WatcherState.STOPPED,)
            and not self._stop_event.is_set()
        )

    def _setup_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        except (ValueError, RuntimeError):
            pass

    def _signal_handler(self, signum: int, _frame: Any) -> None:
        self._logger.info("Received signal %d", signum)
        self.stop()

    def get_last_poll_time(self) -> Optional[str]:
        return self._storage.get_last_poll_time(self.platform)

    def get_stats(self) -> dict[str, Any]:
        return {
            "platform": self.platform.value,
            "state": self._state.value,
            "poll_count": self._poll_count,
            "error_count": self._error_count,
            "consecutive_failures": self._consecutive_failures,
            "uptime_seconds": self.uptime,
            "last_poll": (
                self._last_poll_time.isoformat() if self._last_poll_time else None
            ),
            "last_heartbeat": (
                self._last_heartbeat_time.isoformat()
                if self._last_heartbeat_time
                else None
            ),
            "poll_interval": self._config.poll_interval_seconds,
            "enabled": self._config.enabled,
        }

    def _load_state(self) -> None:
        try:
            self._poll_count = (
                self._storage.load_state(self.platform, "poll_count") or 0
            )
            self._error_count = (
                self._storage.load_state(self.platform, "error_count") or 0
            )
            self._consecutive_failures = (
                self._storage.load_state(self.platform, "consecutive_failures") or 0
            )
            last_poll = self._storage.load_state(self.platform, "last_poll_time")
            if last_poll:
                self._last_poll_time = datetime.fromisoformat(last_poll)
        except Exception:
            self._logger.debug("No prior state found, starting fresh")

    def _save_state(self) -> None:
        try:
            self._storage.save_state(self.platform, "poll_count", self._poll_count)
            self._storage.save_state(self.platform, "error_count", self._error_count)
            self._storage.save_state(
                self.platform, "consecutive_failures", self._consecutive_failures
            )
            if self._last_poll_time:
                self._storage.save_state(
                    self.platform,
                    "last_poll_time",
                    self._last_poll_time.isoformat(),
                )
            if self._last_heartbeat_time:
                self._storage.set_last_poll_time(
                    self.platform, self._last_heartbeat_time.isoformat()
                )
        except Exception:
            self._logger.exception("Failed to save state")
