from collections import defaultdict
from datetime import datetime, timezone
from threading import RLock
from typing import Optional

from golden_tier_external_world.config.enums import EventType, PlatformType
from golden_tier_external_world.events.models import BaseEvent


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = RLock()
        self._total_published: int = 0
        self._total_handled: int = 0
        self._total_errors: int = 0
        self._by_event_type: dict[str, int] = defaultdict(int)
        self._by_platform: dict[str, int] = defaultdict(int)
        self._by_handler: dict[str, int] = defaultdict(int)
        self._handler_errors: dict[str, int] = defaultdict(int)
        self._post_counts: dict[str, int] = defaultdict(int)
        self._start_time: datetime = datetime.now(timezone.utc)

        self._posts_generated: int = 0
        self._posts_published: int = 0
        self._posts_failed: int = 0
        self._retry_count: int = 0
        self._queue_size_peak: int = 0
        self._generation_latencies: list[float] = []
        self._posting_latencies: list[float] = []
        self._last_queue_size: int = 0

    def record_published(self, event: BaseEvent) -> None:
        with self._lock:
            self._total_published += 1
            self._by_event_type[event.event_type.name] += 1
            self._by_platform[event.platform.value] += 1

    def record_handled(
        self,
        event: BaseEvent,
        handler_name: str,
    ) -> None:
        with self._lock:
            self._total_handled += 1
            self._by_handler[handler_name] += 1

    def record_error(
        self,
        handler_name: str,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._total_errors += 1
            self._handler_errors[handler_name] += 1

    def record_post(
        self,
        platform: str,
        status: str,
        source_event_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._post_counts[f"post:{platform}:{status}"] += 1
            if status == "ok":
                self._posts_published += 1
            else:
                self._posts_failed += 1

    def record_generated(self) -> None:
        with self._lock:
            self._posts_generated += 1

    def record_retry(self) -> None:
        with self._lock:
            self._retry_count += 1

    def record_generation_latency(self, seconds: float) -> None:
        with self._lock:
            self._generation_latencies.append(seconds)

    def record_posting_latency(self, seconds: float) -> None:
        with self._lock:
            self._posting_latencies.append(seconds)

    def update_queue_size(self, size: int) -> None:
        with self._lock:
            self._last_queue_size = size
            if size > self._queue_size_peak:
                self._queue_size_peak = size

    def post_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._post_counts)

    def snapshot(self) -> dict:
        with self._lock:
            avg_gen = (
                sum(self._generation_latencies) / len(self._generation_latencies)
                if self._generation_latencies
                else 0.0
            )
            avg_post = (
                sum(self._posting_latencies) / len(self._posting_latencies)
                if self._posting_latencies
                else 0.0
            )
            total_posts = self._posts_published + self._posts_failed
            success_rate = (self._posts_published / total_posts * 100) if total_posts > 0 else 0.0

            return {
                "total_published": self._total_published,
                "total_handled": self._total_handled,
                "total_errors": self._total_errors,
                "by_event_type": dict(self._by_event_type),
                "by_platform": dict(self._by_platform),
                "by_handler": dict(self._by_handler),
                "handler_errors": dict(self._handler_errors),
                "uptime_seconds": (
                    datetime.now(timezone.utc) - self._start_time
                ).total_seconds(),
                "posts_generated": self._posts_generated,
                "posts_published": self._posts_published,
                "posts_failed": self._posts_failed,
                "retry_count": self._retry_count,
                "queue_size_peak": self._queue_size_peak,
                "last_queue_size": self._last_queue_size,
                "avg_generation_latency_ms": round(avg_gen * 1000, 2),
                "avg_posting_latency_ms": round(avg_post * 1000, 2),
                "success_rate_pct": round(success_rate, 2),
            }

    @property
    def event_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._by_event_type)

    @property
    def platform_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._by_platform)
