from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from threading import Thread, Event, RLock
from datetime import datetime, timezone, timedelta
import logging
import time


ScheduleHandler = Callable[..., Any]


@dataclass
class ScheduledTask:
    task_id: str
    handler: ScheduleHandler
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    interval_seconds: Optional[float] = None
    delay_seconds: Optional[float] = None
    cron_expr: Optional[str] = None
    next_run: Optional[datetime] = None
    last_run: Optional[datetime] = None
    run_count: int = 0
    max_runs: Optional[int] = None
    enabled: bool = True

    @property
    def is_due(self) -> bool:
        if not self.enabled:
            return False
        if self.max_runs is not None and self.run_count >= self.max_runs:
            return False
        if self.next_run is None:
            return True
        return datetime.now(timezone.utc) >= self.next_run


class Scheduler:
    def __init__(self, poll_interval: float = 0.1) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._lock = RLock()
        self._running = False
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
        self._logger = logging.getLogger(self.__class__.__name__)
        self._poll_interval = poll_interval

    def add_interval(
        self,
        task_id: str,
        handler: ScheduleHandler,
        interval_seconds: float,
        *args: Any,
        max_runs: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        with self._lock:
            task = ScheduledTask(
                task_id=task_id,
                handler=handler,
                args=args,
                kwargs=kwargs,
                interval_seconds=interval_seconds,
                next_run=datetime.now(timezone.utc) + timedelta(seconds=interval_seconds),
                max_runs=max_runs,
            )
            self._tasks[task_id] = task
        self._logger.info(
            "Scheduled interval task | id=%s | interval=%ds | max_runs=%s",
            task_id, interval_seconds, max_runs,
        )
        return task_id

    def add_delayed(
        self,
        task_id: str,
        handler: ScheduleHandler,
        delay_seconds: float,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        with self._lock:
            task = ScheduledTask(
                task_id=task_id,
                handler=handler,
                args=args,
                kwargs=kwargs,
                delay_seconds=delay_seconds,
                next_run=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
                max_runs=1,
            )
            self._tasks[task_id] = task
        self._logger.info(
            "Scheduled delayed task | id=%s | delay=%ds",
            task_id, delay_seconds,
        )
        return task_id

    def remove(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                self._logger.info("Removed scheduled task | id=%s", task_id)
                return True
            return False

    def disable(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.enabled = False
            return True

    def enable(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.enabled = True
            return True

    @property
    def task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run_loop,
            name="scheduler",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Scheduler started | tasks=%d", self.task_count)

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._logger.info("Scheduler stopped")

    def _run_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            due_tasks = self._collect_due()
            for task in due_tasks:
                self._execute(task)
            time.sleep(self._poll_interval)

    def _collect_due(self) -> list[ScheduledTask]:
        due: list[ScheduledTask] = []
        with self._lock:
            for task in self._tasks.values():
                if task.is_due:
                    due.append(task)
        return due

    def _execute(self, task: ScheduledTask) -> None:
        try:
            if task.max_runs is not None and task.run_count >= task.max_runs:
                return
            task.run_count += 1
            task.last_run = datetime.now(timezone.utc)
            self._logger.debug(
                "Executing scheduled task | id=%s | run=%d",
                task.task_id, task.run_count,
            )
            task.handler(*task.args, **task.kwargs)
        except Exception:
            self._logger.exception(
                "Scheduled task failed | id=%s | run=%d",
                task.task_id, task.run_count,
            )
        finally:
            self._reschedule(task)

    def _reschedule(self, task: ScheduledTask) -> None:
        with self._lock:
            if task.interval_seconds is not None:
                task.next_run = datetime.now(timezone.utc) + timedelta(
                    seconds=task.interval_seconds,
                )
            else:
                task.enabled = False

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "interval_seconds": t.interval_seconds,
                    "next_run": t.next_run.isoformat() if t.next_run else None,
                    "last_run": t.last_run.isoformat() if t.last_run else None,
                    "run_count": t.run_count,
                    "max_runs": t.max_runs,
                    "enabled": t.enabled,
                }
                for t in self._tasks.values()
            ]
