from __future__ import annotations

from pathlib import Path
from threading import Thread, Event, Lock
from typing import Any, Optional, Callable
import logging
import time

from golden_tier_external_world.config.enums import EventType, PlatformType
from golden_tier_external_world.events.models import BaseEvent, MessageEvent
from golden_tier_external_world.events.bus import EventBus, Priority
from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.content_orchestrator.generator import ContentGenerator
from golden_tier_external_world.content_orchestrator.dedup import ContentDedup
from golden_tier_external_world.content_orchestrator.queue import (
    ContentQueue,
    ContentQueueItem,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_IN_PROGRESS,
    QUEUE_STATUS_DONE,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_DLQ,
    QUEUE_STATUS_SCHEDULED,
    QUEUE_PRIORITY_CRITICAL,
    QUEUE_PRIORITY_HIGH,
    QUEUE_PRIORITY_MEDIUM,
    QUEUE_PRIORITY_LOW,
)
from golden_tier_external_world.content_orchestrator.validator import ContentValidator, ValidationResult
from golden_tier_external_world.content_orchestrator.rate_limiter import RateLimiter
from golden_tier_external_world.content_orchestrator.post_result import PostResult
from golden_tier_external_world.process_manager.manager import ProcessManager


_RESPOND_EVENT_TYPES = {
    EventType.MESSAGE,
    EventType.COMMENT,
    EventType.MENTION,
}

_IGNORE_EVENT_TYPES = {
    EventType.LIKE,
    EventType.PROFILE_VIEW,
    EventType.FOLLOW,
    EventType.NOTIFICATION,
}

_TARGET_PLATFORMS = [
    PlatformType.FACEBOOK,
    PlatformType.TWITTER,
    PlatformType.INSTAGRAM,
]

_QUEUE_POLL_INTERVAL = 2.0
_DLQ_REPLAY_INTERVAL = 10  # replay all DLQ items every N loop iterations

PlannerCallback = Callable[[str, dict[str, Any]], None]


class ContentEngine:
    def __init__(
        self,
        storage: StorageInterface,
        event_bus: EventBus,
        metrics: MetricsCollector,
        generator: ContentGenerator,
        dedup: ContentDedup,
        queue: ContentQueue,
        process_manager: ProcessManager,
        validator: Optional[ContentValidator] = None,
        rate_limiter: Optional[RateLimiter] = None,
        planner_callback: Optional[PlannerCallback] = None,
        respond_event_types: Optional[set[EventType]] = None,
        target_platforms: Optional[list[PlatformType]] = None,
        dlq_replay_interval: int = _DLQ_REPLAY_INTERVAL,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._metrics = metrics
        self._generator = generator
        self._dedup = dedup
        self._queue = queue
        self._process_manager = process_manager
        self._validator = validator or ContentValidator()
        self._rate_limiter = rate_limiter or RateLimiter()
        self._planner_callback = planner_callback
        self._respond_event_types = respond_event_types or _RESPOND_EVENT_TYPES
        self._target_platforms = target_platforms or _TARGET_PLATFORMS
        self._dlq_replay_interval = dlq_replay_interval
        self._logger = logging.getLogger(self.__class__.__name__)

        self._running = False
        self._stop_event = Event()
        self._processor_thread: Optional[Thread] = None
        self._subscriptions: list[object] = []
        self._loop_iterations = 0

    def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._stop_event.clear()

        self._loop_iterations = 0

        self._processor_thread = Thread(
            target=self._process_queue_loop,
            name="content-engine",
            daemon=True,
        )
        self._processor_thread.start()

        self._logger.info("ContentEngine started")

    def stop(self, timeout: float = 10.0) -> None:
        self._running = False
        self._stop_event.set()

        for sub in self._subscriptions:
            self._event_bus.unsubscribe(sub)
        self._subscriptions.clear()

        if self._processor_thread and self._processor_thread.is_alive():
            self._processor_thread.join(timeout=timeout)

        self._logger.info("ContentEngine stopped")

    def register(self) -> None:
        for et in self._respond_event_types:
            sub = self._event_bus.subscribe(
                event_type=et,
                handler=self._on_event,
                priority=Priority.MEDIUM,
                name=f"ContentEngine:{et.name}",
            )
            self._subscriptions.append(sub)
        self._logger.info(
            "ContentEngine registered for %d event types",
            len(self._subscriptions),
        )

    def set_planner_callback(self, callback: PlannerCallback) -> None:
        self._planner_callback = callback

    def _notify_planner(self, phase: str, data: dict[str, Any]) -> None:
        if self._planner_callback:
            try:
                self._planner_callback(phase, data)
            except Exception as e:
                self._logger.error("Planner callback failed | phase=%s | error=%s", phase, e)

    def _on_event(self, event: BaseEvent) -> dict[str, Any]:
        self._logger.info(
            "Event received | id=%s | type=%s | platform=%s",
            event.event_id,
            event.event_type.name,
            event.platform.value,
        )

        if event.event_type in _IGNORE_EVENT_TYPES:
            self._logger.debug("Ignored event type | type=%s", event.event_type.name)
            return {"status": "ignored", "reason": "unsupported_event_type"}

        self._notify_planner("generation_started", {
            "event_id": event.event_id,
            "event_type": event.event_type.name,
            "platform": event.platform.value,
        })

        result = self._process_event(event)

        if result.get("status") == "ok":
            self._notify_planner("generation_finished", {
                "event_id": event.event_id,
                "enqueued_platforms": result.get("enqueued_platforms", []),
            })
        else:
            self._notify_planner("generation_failed", {
                "event_id": event.event_id,
                "reason": result.get("reason", "unknown"),
            })

        self._metrics.record_handled(event, self.__class__.__name__)
        if result.get("status") == "error":
            self._metrics.record_error(self.__class__.__name__, result.get("error"))
            self._notify_planner("posting_failed", {
                "event_id": event.event_id,
                "error": result.get("error"),
            })

        return result

    def _process_event(self, event: BaseEvent) -> dict[str, Any]:
        if event.event_type not in self._respond_event_types:
            return {"status": "skipped", "reason": "unsupported_event_type"}

        source_platform = event.platform
        generated = False
        enqueued_platforms: list[str] = []
        errors: list[str] = []

        for target_platform in self._target_platforms:
            if self._dedup.is_posted(event, target_platform):
                self._logger.debug(
                    "Skipped (already posted) | event_id=%s | platform=%s",
                    event.event_id,
                    target_platform.value,
                )
                continue

            if not self._rate_limiter.allow(target_platform):
                self._logger.warning(
                    "Rate limited | event_id=%s | platform=%s",
                    event.event_id,
                    target_platform.value,
                )
                errors.append(f"rate_limited:{target_platform.value}")
                continue

            try:
                post_content = self._generator.generate(event, target_platform)
            except Exception as e:
                self._logger.error(
                    "Content generation failed | event_id=%s | platform=%s | error=%s",
                    event.event_id,
                    target_platform.value,
                    e,
                )
                self._metrics.record_error(
                    self.__class__.__name__,
                    f"generate:{target_platform.value}:{e}",
                )
                errors.append(f"generate:{target_platform.value}:{e}")
                continue

            if post_content is None:
                self._logger.warning(
                    "Generator returned None | event_id=%s | platform=%s",
                    event.event_id,
                    target_platform.value,
                )
                errors.append(f"no_content:{target_platform.value}")
                continue

            self._metrics.record_generated()

            if self._dedup.is_content_posted(event, target_platform, post_content.text):
                self._logger.debug(
                    "Duplicate content skipped | event_id=%s | platform=%s",
                    event.event_id,
                    target_platform.value,
                )
                errors.append(f"duplicate_content:{target_platform.value}")
                continue

            validation = self._validator.validate(post_content, target_platform)
            if not validation.valid:
                self._logger.warning(
                    "Validation failed | event_id=%s | platform=%s | errors=%s",
                    event.event_id,
                    target_platform.value,
                    validation.errors,
                )
                errors.append(f"validation:{target_platform.value}:{validation.errors}")
                continue

            for warning in validation.warnings:
                self._logger.warning(
                    "Validation warning | event_id=%s | platform=%s | warning=%s",
                    event.event_id,
                    target_platform.value,
                    warning,
                )

            item_id = self._queue.enqueue(
                source_event_id=event.event_id,
                platform=target_platform,
                text=post_content.text,
                media_paths=[str(p) for p in post_content.media_paths],
                max_retries=3,
            )

            self._dedup.mark_posted(
                event, target_platform,
                text=post_content.text,
            )
            enqueued_platforms.append(target_platform.value)
            generated = True

            self._logger.info(
                "Content queued | event_id=%s | platform=%s | queue_id=%s | text_len=%d",
                event.event_id,
                target_platform.value,
                item_id,
                len(post_content.text),
            )

        self._metrics.update_queue_size(self._queue.pending_count())

        return {
            "status": "ok" if generated else "skipped",
            "event_id": event.event_id,
            "enqueued_platforms": enqueued_platforms,
            "source_platform": source_platform.value,
            "errors": errors if errors else None,
        }

    def _process_queue_loop(self) -> None:
        self._logger.info("Queue processor started")

        while not self._stop_event.is_set():
            self._process_one_item()

            self._loop_iterations += 1
            if self._loop_iterations % self._dlq_replay_interval == 0:
                self._replay_dlq_items()

            self._stop_event.wait(_QUEUE_POLL_INTERVAL)

        self._logger.info("Queue processor stopped")

    def _replay_dlq_items(self) -> None:
        count = self._queue.dlq_count()
        if count == 0:
            return

        self._logger.info("Replaying %d items from DLQ", count)
        replayed = self._queue.replay_all_dlq()
        if replayed:
            self._queue.clear_done()
            self._logger.info("Replayed %d DLQ items back to pending queue", replayed)

    def _process_one_item(self) -> None:
        item = self._queue.dequeue()
        if item is None:
            return

        self._logger.info(
            "Processing queue item | id=%s | platform=%s | retry=%d/%d",
            item.item_id,
            item.platform,
            item.retry_count,
            item.max_retries,
        )

        post_start = time.time()
        post_result = PostResult(
            platform=item.platform,
            source_event_id=item.source_event_id,
            queue_item_id=item.item_id,
            retry_count=item.retry_count,
        )

        self._notify_planner("posting_started", {
            "event_id": item.source_event_id,
            "queue_item_id": item.item_id,
            "platform": item.platform,
        })

        try:
            platform_type = PlatformType(item.platform)
            if not self._rate_limiter.consume(platform_type):
                error_msg = f"Rate limit exceeded for {item.platform}"
                self._queue.nack(item.item_id, error=error_msg, requeue=True)
                self._metrics.record_post(
                    platform=item.platform,
                    status="failed",
                    source_event_id=item.source_event_id,
                )
                self._metrics.record_retry()
                post_result.status = "failed"
                post_result.error = error_msg
                self._notify_planner("retry_scheduled", {
                    "event_id": item.source_event_id,
                    "queue_item_id": item.item_id,
                    "error": error_msg,
                    "retry_count": item.retry_count,
                })
                return

            post_id = self._send_to_platform(item)

            post_duration = time.time() - post_start
            self._metrics.record_posting_latency(post_duration)
            post_result.duration_ms = round(post_duration * 1000, 2)

            if post_id:
                self._queue.ack(item.item_id, post_id=post_id)
                self._metrics.record_post(
                    platform=item.platform,
                    status="ok",
                    source_event_id=item.source_event_id,
                )
                self._logger.info(
                    "Post succeeded | queue_id=%s | platform=%s | post_id=%s",
                    item.item_id,
                    item.platform,
                    post_id,
                )
                post_result.status = "ok"
                post_result.post_id = post_id
                self._notify_planner("posting_finished", {
                    "event_id": item.source_event_id,
                    "queue_item_id": item.item_id,
                    "platform": item.platform,
                    "post_id": post_id,
                    "duration_ms": post_result.duration_ms,
                })
            else:
                error_msg = f"Post returned no post_id for platform {item.platform}"
                self._queue.nack(item.item_id, error=error_msg, requeue=True)
                self._metrics.record_post(
                    platform=item.platform,
                    status="failed",
                    source_event_id=item.source_event_id,
                )
                self._metrics.record_retry()
                post_result.status = "failed"
                post_result.error = error_msg
                self._notify_planner("retry_scheduled", {
                    "event_id": item.source_event_id,
                    "queue_item_id": item.item_id,
                    "error": error_msg,
                    "retry_count": item.retry_count,
                })
                self._logger.warning(
                    "Post failed (no id) | queue_id=%s | platform=%s",
                    item.item_id,
                    item.platform,
                )

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            post_duration = time.time() - post_start
            self._metrics.record_posting_latency(post_duration)
            post_result.duration_ms = round(post_duration * 1000, 2)

            self._logger.warning(
                "Post failed | queue_id=%s | platform=%s | error=%s",
                item.item_id,
                item.platform,
                error_msg,
            )
            self._queue.nack(item.item_id, error=error_msg, requeue=True)
            self._metrics.record_post(
                platform=item.platform,
                status="failed",
                source_event_id=item.source_event_id,
            )
            self._metrics.record_retry()
            post_result.status = "failed"
            post_result.error = error_msg
            self._notify_planner("retry_scheduled", {
                "event_id": item.source_event_id,
                "queue_item_id": item.item_id,
                "error": error_msg,
                "retry_count": item.retry_count,
            })

        if hasattr(self._storage, "save_state"):
            try:
                self._storage.save_state(
                    item.platform,
                    f"post_result:{item.item_id}",
                    post_result.to_dict(),
                )
            except Exception as e:
                self._logger.error("Failed to save post result | error=%s", e)

    def _send_to_platform(self, item: ContentQueueItem) -> Optional[str]:
        platform_type = PlatformType(item.platform)

        event_data = {
            "event_type": "post",
            "data": {
                "text": item.text,
                "media_paths": item.media_paths,
            },
        }

        result = self._process_manager.send_event(
            platform=platform_type,
            event_data=event_data,
            timeout=60.0,
        )

        if result is None:
            self._logger.warning(
                "No result from process manager | queue_id=%s | platform=%s",
                item.item_id,
                item.platform,
            )
            return None

        if result.get("status") == "ok":
            data = result.get("data", {})
            return data.get("post_id")

        error = result.get("error", "unknown error")
        self._logger.warning(
            "Platform returned error | queue_id=%s | platform=%s | error=%s",
            item.item_id,
            item.platform,
            error,
        )
        return None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def queue(self) -> ContentQueue:
        return self._queue

    def process_event_sync(self, event: BaseEvent) -> dict[str, Any]:
        self._notify_planner("generation_started", {
            "event_id": event.event_id,
            "event_type": event.event_type.name,
            "platform": event.platform.value,
        })

        result = self._process_event(event)

        if result.get("status") == "ok":
            self._notify_planner("generation_finished", {
                "event_id": event.event_id,
                "enqueued_platforms": result.get("enqueued_platforms", []),
            })
        else:
            self._notify_planner("generation_failed", {
                "event_id": event.event_id,
                "reason": result.get("reason", "unknown"),
            })

        return result
