from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Optional
import logging


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    name: str
    status: HealthStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


HealthCheckFn = Callable[[], HealthCheckResult]


class HealthRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, HealthCheckFn] = {}
        self._results: dict[str, HealthCheckResult] = {}
        self._lock = RLock()
        self._logger = logging.getLogger("health")

    def register(self, name: str, check_fn: HealthCheckFn) -> None:
        with self._lock:
            self._checks[name] = check_fn
            self._logger.debug("Health check registered | name=%s", name)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._checks.pop(name, None)
            self._results.pop(name, None)

    def run_check(self, name: str) -> Optional[HealthCheckResult]:
        with self._lock:
            check_fn = self._checks.get(name)
            if check_fn is None:
                return None

        try:
            result = check_fn()
        except Exception as e:
            result = HealthCheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

        with self._lock:
            self._results[name] = result

        return result

    def run_all(self) -> dict[str, HealthCheckResult]:
        with self._lock:
            names = list(self._checks.keys())

        results: dict[str, HealthCheckResult] = {}
        for name in names:
            result = self.run_check(name)
            if result:
                results[name] = result

        return results

    def get_result(self, name: str) -> Optional[HealthCheckResult]:
        with self._lock:
            return self._results.get(name)

    def overall_status(self) -> HealthStatus:
        with self._lock:
            if not self._results:
                return HealthStatus.UNHEALTHY

            statuses = [r.status for r in self._results.values()]
            if any(s == HealthStatus.UNHEALTHY for s in statuses):
                return HealthStatus.UNHEALTHY
            if any(s == HealthStatus.DEGRADED for s in statuses):
                return HealthStatus.DEGRADED
            return HealthStatus.HEALTHY

    def aggregate(self) -> dict[str, Any]:
        self.run_all()
        with self._lock:
            return {
                "status": self.overall_status().value,
                "checks": {
                    name: {
                        "status": result.status.value,
                        "message": result.message,
                        "checked_at": result.checked_at,
                        "details": result.details,
                    }
                    for name, result in self._results.items()
                },
            }

    @property
    def check_count(self) -> int:
        with self._lock:
            return len(self._checks)

    @property
    def registered_checks(self) -> list[str]:
        with self._lock:
            return list(self._checks.keys())
