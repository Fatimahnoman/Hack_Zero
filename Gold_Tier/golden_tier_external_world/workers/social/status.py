from enum import Enum, auto
from typing import Optional, Any
from datetime import datetime, timezone
from threading import RLock


class WorkerStatus(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()
    ERROR = auto()


class StatusTracker:
    def __init__(self) -> None:
        self._lock = RLock()
        self._status: WorkerStatus = WorkerStatus.IDLE
        self._started_at: Optional[datetime] = None
        self._last_activity: Optional[datetime] = None
        self._error_count: int = 0
        self._processed_count: int = 0
        self._failed_count: int = 0
        self._last_error: Optional[str] = None

    @property
    def status(self) -> WorkerStatus:
        with self._lock:
            return self._status

    @status.setter
    def status(self, value: WorkerStatus) -> None:
        with self._lock:
            was_idle = self._status == WorkerStatus.IDLE
            self._status = value
            if was_idle and value == WorkerStatus.RUNNING:
                self._started_at = datetime.now(timezone.utc)
            self._last_activity = datetime.now(timezone.utc)

    @property
    def uptime(self) -> Optional[float]:
        with self._lock:
            if self._started_at is None:
                return None
            return (datetime.now(timezone.utc) - self._started_at).total_seconds()

    @property
    def is_running(self) -> bool:
        return self.status == WorkerStatus.RUNNING

    @property
    def is_stopped(self) -> bool:
        return self.status in (WorkerStatus.STOPPED, WorkerStatus.ERROR)

    def record_success(self) -> None:
        with self._lock:
            self._processed_count += 1
            self._last_activity = datetime.now(timezone.utc)

    def record_failure(self, error: Optional[str] = None) -> None:
        with self._lock:
            self._failed_count += 1
            self._error_count += 1
            self._last_error = error
            self._last_activity = datetime.now(timezone.utc)

    def reset(self) -> None:
        with self._lock:
            self._status = WorkerStatus.IDLE
            self._started_at = None
            self._last_activity = None
            self._error_count = 0
            self._processed_count = 0
            self._failed_count = 0
            self._last_error = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._status.name,
                "uptime_seconds": self.uptime,
                "processed_count": self._processed_count,
                "failed_count": self._failed_count,
                "error_count": self._error_count,
                "last_error": self._last_error,
                "last_activity": (
                    self._last_activity.isoformat() if self._last_activity else None
                ),
            }
