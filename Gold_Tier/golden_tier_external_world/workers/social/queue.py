from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from enum import IntEnum, auto
from queue import PriorityQueue
from threading import RLock
from datetime import datetime, timezone
from uuid import uuid4


class TaskPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


TaskHandler = Callable[..., Any]


@dataclass(order=True)
class _QueueItem:
    priority: int
    timestamp: float
    task: "Task" = field(compare=False)


@dataclass
class Task:
    task_id: str
    handler: TaskHandler
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.MEDIUM
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Any = None

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    @property
    def failed(self) -> bool:
        return self.retry_count >= self.max_retries


class TaskQueue:
    def __init__(self, maxsize: int = 0) -> None:
        self._queue: PriorityQueue[_QueueItem] = PriorityQueue(maxsize=maxsize)
        self._lock = RLock()
        self._pending: dict[str, Task] = {}
        self._processing: dict[str, Task] = {}
        self._total_enqueued: int = 0

    def enqueue(
        self,
        handler: TaskHandler,
        *args: Any,
        task_id: Optional[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> str:
        tid = task_id or uuid4().hex
        task = Task(
            task_id=tid,
            handler=handler,
            args=args,
            kwargs=kwargs,
            priority=priority,
            max_retries=max_retries,
        )
        item = _QueueItem(
            priority=priority.value,
            timestamp=task.created_at.timestamp(),
            task=task,
        )
        with self._lock:
            self._queue.put(item)
            self._pending[tid] = task
            self._total_enqueued += 1
        return tid

    def dequeue(self, timeout: float = 1.0) -> Optional[Task]:
        try:
            item = self._queue.get(timeout=timeout)
            task = item.task
            with self._lock:
                self._pending.pop(task.task_id, None)
                task.started_at = datetime.now(timezone.utc)
                self._processing[task.task_id] = task
            return task
        except Exception:
            return None

    def ack(self, task_id: str, result: Any = None) -> bool:
        with self._lock:
            task = self._processing.pop(task_id, None)
            if task is None:
                return False
            task.completed_at = datetime.now(timezone.utc)
            task.result = result
            return True

    def nack(
        self,
        task_id: str,
        requeue: bool = True,
        error: Optional[str] = None,
    ) -> bool:
        with self._lock:
            task = self._processing.pop(task_id, None)
            if task is None:
                return False
            task.retry_count += 1
            task.error = error
            if requeue and not task.failed:
                item = _QueueItem(
                    priority=task.priority.value,
                    timestamp=datetime.now(timezone.utc).timestamp(),
                    task=task,
                )
                self._queue.put(item)
                self._pending[task_id] = task
            return True

    def retry_later(
        self,
        task_id: str,
        delay_seconds: float = 5.0,
        error: Optional[str] = None,
    ) -> bool:
        from time import time
        with self._lock:
            task = self._processing.pop(task_id, None)
            if task is None:
                return False
            task.retry_count += 1
            task.error = error
            if not task.failed:
                item = _QueueItem(
                    priority=task.priority.value,
                    timestamp=time() + delay_seconds,
                    task=task,
                )
                self._queue.put(item)
                self._pending[task_id] = task
            return True

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def processing_count(self) -> int:
        with self._lock:
            return len(self._processing)

    @property
    def total_enqueued(self) -> int:
        with self._lock:
            return self._total_enqueued

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def clear(self) -> int:
        cleared = 0
        with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    cleared += 1
                except Exception:
                    break
            self._pending.clear()
            self._processing.clear()
        return cleared

    def pending_tasks(self) -> list[Task]:
        with self._lock:
            return list(self._pending.values())

    def processing_tasks(self) -> list[Task]:
        with self._lock:
            return list(self._processing.values())
