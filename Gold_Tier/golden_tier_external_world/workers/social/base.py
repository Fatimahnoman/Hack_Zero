from abc import abstractmethod
from typing import Any, Optional
from threading import Thread, Event
from datetime import datetime, timezone
import logging
import time
import random

from golden_tier_external_world.events.models import BaseEvent
from golden_tier_external_world.events.bus import EventBus
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.workers.base import BaseWorker
from golden_tier_external_world.workers.social.status import WorkerStatus, StatusTracker
from golden_tier_external_world.workers.social.queue import TaskQueue, TaskPriority, Task
from golden_tier_external_world.workers.social.scheduler import Scheduler


class BaseSocialWorker(BaseWorker):
    def __init__(
        self,
        storage: StorageInterface,
        event_bus: EventBus,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        backoff_jitter: float = 0.5,
        health_check_interval: float = 30.0,
        max_consecutive_failures: int = 10,
    ) -> None:
        super().__init__(storage=storage, event_bus=event_bus)
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._backoff_jitter = backoff_jitter
        self._health_check_interval = health_check_interval
        self._max_consecutive_failures = max_consecutive_failures

        self._status = StatusTracker()
        self._task_queue = TaskQueue()
        self._scheduler = Scheduler()
        self._consecutive_failures: int = 0
        self._stop_event = Event()
        self._processor_thread: Optional[Thread] = None
        self._health_thread: Optional[Thread] = None
        self._last_health_check: float = 0.0
        self._start_time: Optional[datetime] = None
        self._logger = logging.getLogger(self.__class__.__name__)

    # ── Properties ─────────────────────────────────────────────

    @property
    def worker_status(self) -> WorkerStatus:
        return self._status.status

    @property
    def status_snapshot(self) -> dict[str, Any]:
        return self._status.snapshot()

    @property
    def task_queue(self) -> TaskQueue:
        return self._task_queue

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    @property
    def uptime(self) -> Optional[float]:
        if self._start_time is None:
            return None
        return (datetime.now(timezone.utc) - self._start_time).total_seconds()

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        if self._status.is_running:
            self._logger.warning("Worker already running")
            return

        self._start_time = datetime.now(timezone.utc)
        self._status.status = WorkerStatus.RUNNING
        self._stop_event.clear()

        self._processor_thread = Thread(
            target=self._process_loop,
            name=f"{self.__class__.__name__}-processor",
            daemon=True,
        )
        self._processor_thread.start()

        self._scheduler.start()

        self._logger.info(
            "Worker started | class=%s | max_retries=%d",
            self.__class__.__name__,
            self._max_retries,
        )

    def stop(self) -> None:
        self._logger.info(
            "Worker stopping | class=%s | processed=%d | failed=%d",
            self.__class__.__name__,
            self._status.snapshot()["processed_count"],
            self._status.snapshot()["failed_count"],
        )
        self._status.status = WorkerStatus.STOPPED
        self._stop_event.set()
        self._scheduler.stop()

    def graceful_shutdown(self, timeout: float = 10.0) -> None:
        self._logger.info(
            "Graceful shutdown | class=%s",
            self.__class__.__name__,
        )
        self.stop()
        if self._processor_thread and self._processor_thread.is_alive():
            self._processor_thread.join(timeout=timeout)
        self._logger.info("Shutdown complete | class=%s", self.__class__.__name__)

    def pause(self) -> None:
        if self._status.is_running:
            self._status.status = WorkerStatus.PAUSED
            self._logger.info("Worker paused | class=%s", self.__class__.__name__)

    def resume(self) -> None:
        if self._status.status == WorkerStatus.PAUSED:
            self._status.status = WorkerStatus.RUNNING
            self._logger.info("Worker resumed | class=%s", self.__class__.__name__)

    # ── Processing Loop ────────────────────────────────────────

    def _process_loop(self) -> None:
        while self._should_continue():
            if self._status.status == WorkerStatus.PAUSED:
                time.sleep(0.5)
                continue

            self._perform_health_check()

            task = self._task_queue.dequeue(timeout=1.0)
            if task is None:
                continue

            self._status.status = WorkerStatus.RUNNING
            self._execute_with_retry(task)

        self._logger.info("Process loop ended | class=%s", self.__class__.__name__)

    def _execute_with_retry(self, task: Task) -> None:
        attempt = 0
        while attempt <= task.max_retries:
            try:
                result = task.handler(*task.args, **task.kwargs)
                self._task_queue.ack(task.task_id, result=result)
                self._status.record_success()
                self._consecutive_failures = 0
                self._logger.debug(
                    "Task completed | id=%s | handler=%s",
                    task.task_id,
                    task.handler.__name__,
                )
                return

            except Exception as e:
                attempt += 1
                task.retry_count = attempt
                error_msg = f"{type(e).__name__}: {e}"
                self._logger.warning(
                    "Task failed | id=%s | handler=%s | attempt=%d/%d | error=%s",
                    task.task_id,
                    task.handler.__name__,
                    attempt,
                    task.max_retries + 1,
                    error_msg,
                )

                if attempt <= task.max_retries:
                    delay = self._backoff(attempt)
                    if self._interruptible_sleep(delay):
                        return

        self._task_queue.nack(task.task_id, requeue=False, error=error_msg)
        self._status.record_failure(error=error_msg)
        self._consecutive_failures += 1
        self._logger.error(
            "Task failed after %d attempts | id=%s | handler=%s",
            attempt,
            task.task_id,
            task.handler.__name__,
        )

    def _backoff(self, attempt: int) -> float:
        delay = min(self._backoff_max, self._backoff_base ** attempt)
        jitter = random.uniform(0, self._backoff_jitter)
        return delay + jitter

    def _interruptible_sleep(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return True
            remaining = deadline - time.monotonic()
            time.sleep(min(0.25, max(0.05, remaining)))
        return False

    def _should_continue(self) -> bool:
        return self._status.status not in (WorkerStatus.STOPPED,)

    # ── Health ─────────────────────────────────────────────────

    def _perform_health_check(self) -> None:
        now = time.monotonic()
        if now - self._last_health_check < self._health_check_interval:
            return
        self._last_health_check = now

        if not self._health_check():
            self._logger.warning(
                "Health check failed | class=%s | failures=%d",
                self.__class__.__name__,
                self._consecutive_failures,
            )

    def _health_check(self) -> bool:
        if self._status.status == WorkerStatus.ERROR:
            self._logger.warning("Health: worker in ERROR state")
            return False

        if self._consecutive_failures >= self._max_consecutive_failures:
            self._logger.warning(
                "Health: too many consecutive failures (%d >= %d)",
                self._consecutive_failures,
                self._max_consecutive_failures,
            )
            self._status.status = WorkerStatus.ERROR
            return False

        self._logger.debug(
            "Health check OK | class=%s | failures=%d",
            self.__class__.__name__,
            self._consecutive_failures,
        )
        return True

    def _set_error(self, error: Optional[str] = None) -> None:
        self._status.status = WorkerStatus.ERROR
        self._status.record_failure(error=error)
        self._logger.error(
            "Worker error | class=%s | error=%s",
            self.__class__.__name__,
            error,
        )

    # ── Event Handling ─────────────────────────────────────────

    def process(self, event: BaseEvent) -> dict[str, Any]:
        return self._on_event(event)

    def can_handle(self, event: BaseEvent) -> bool:
        return True

    def _on_event(self, event: BaseEvent) -> dict[str, Any]:
        self._logger.info(
            "Event queued | id=%s | type=%s | platform=%s",
            event.event_id,
            event.event_type.name,
            event.platform.value,
        )

        task_id = self._task_queue.enqueue(
            handler=self.process_event,
            event=event,
            max_retries=self._max_retries,
        )
        return {
            "status": "queued",
            "task_id": task_id,
            "event_id": event.event_id,
        }

    def process_event(self, event: BaseEvent) -> Any:
        if not self.can_handle(event):
            self._logger.debug(
                "Event skipped | id=%s | reason=can_handle returned False",
                event.event_id,
            )
            return {"status": "skipped", "reason": "not_handled"}
        return self._execute(event)

    @abstractmethod
    def _execute(self, event: BaseEvent) -> Any:
        ...
