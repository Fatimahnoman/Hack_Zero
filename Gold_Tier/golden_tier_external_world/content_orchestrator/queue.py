from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4
import logging

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.storage.backends.base import StorageBackend


QUEUE_STATUS_PENDING = "pending"
QUEUE_STATUS_IN_PROGRESS = "in_progress"
QUEUE_STATUS_DONE = "done"
QUEUE_STATUS_FAILED = "failed"
QUEUE_STATUS_SCHEDULED = "scheduled"
QUEUE_STATUS_DLQ = "dead_letter"

QUEUE_PRIORITY_CRITICAL = 0
QUEUE_PRIORITY_HIGH = 1
QUEUE_PRIORITY_MEDIUM = 2
QUEUE_PRIORITY_LOW = 3


@dataclass
class ContentQueueItem:
    item_id: str = field(default_factory=lambda: uuid4().hex)
    source_event_id: str = ""
    platform: str = ""
    text: str = ""
    media_paths: list[str] = field(default_factory=list)
    status: str = QUEUE_STATUS_PENDING
    retry_count: int = 0
    max_retries: int = 3
    priority: int = QUEUE_PRIORITY_MEDIUM
    error: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    scheduled_at: Optional[str] = None
    ttl_seconds: Optional[int] = None
    post_id: Optional[str] = None
    source: str = "content_engine"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentQueueItem:
        return cls(**data)

    @property
    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        try:
            created = datetime.fromisoformat(self.created_at)
            elapsed = (datetime.now(timezone.utc) - created).total_seconds()
            return elapsed > self.ttl_seconds
        except (ValueError, TypeError):
            return False

    @property
    def is_scheduled(self) -> bool:
        if self.scheduled_at is None:
            return False
        try:
            scheduled = datetime.fromisoformat(self.scheduled_at)
            return scheduled > datetime.now(timezone.utc)
        except (ValueError, TypeError):
            return False


class ContentQueue:
    def __init__(
        self,
        backend: StorageBackend,
        dlq_backend: Optional[StorageBackend] = None,
    ) -> None:
        self._backend = backend
        self._dlq_backend = dlq_backend
        self._logger = logging.getLogger(self.__class__.__name__)
        self._items: dict[str, ContentQueueItem] = {}
        self._dlq_items: dict[str, ContentQueueItem] = {}
        self._load()

    def enqueue(
        self,
        source_event_id: str,
        platform: PlatformType | str,
        text: str,
        media_paths: Optional[list[str]] = None,
        max_retries: int = 3,
        priority: int = QUEUE_PRIORITY_MEDIUM,
        scheduled_at: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        source: str = "content_engine",
    ) -> str:
        platform_str = platform.value if isinstance(platform, PlatformType) else platform
        item = ContentQueueItem(
            source_event_id=source_event_id,
            platform=platform_str,
            text=text,
            media_paths=media_paths or [],
            max_retries=max_retries,
            priority=priority,
            scheduled_at=scheduled_at,
            ttl_seconds=ttl_seconds,
            source=source,
        )

        if scheduled_at:
            try:
                scheduled_dt = datetime.fromisoformat(scheduled_at)
                if scheduled_dt > datetime.now(timezone.utc):
                    item.status = QUEUE_STATUS_SCHEDULED
            except (ValueError, TypeError):
                pass

        self._items[item.item_id] = item
        self._save()
        self._logger.info(
            "Enqueued | id=%s | platform=%s | source_event=%s | priority=%d | scheduled=%s",
            item.item_id,
            item.platform,
            item.source_event_id,
            item.priority,
            item.scheduled_at or "now",
        )
        return item.item_id

    def dequeue(
        self,
        platform: Optional[PlatformType | str] = None,
        priority_min: Optional[int] = None,
    ) -> Optional[ContentQueueItem]:
        self._flush_expired()
        self._release_scheduled()

        now = datetime.now(timezone.utc)
        platform_str = platform.value if isinstance(platform, PlatformType) else platform

        candidates: list[ContentQueueItem] = []
        for item in self._items.values():
            if item.status != QUEUE_STATUS_PENDING:
                continue
            if platform_str and item.platform != platform_str:
                continue
            if priority_min is not None and item.priority > priority_min:
                continue
            candidates.append(item)

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x.priority, x.created_at))
        chosen = candidates[0]
        chosen.status = QUEUE_STATUS_IN_PROGRESS
        chosen.updated_at = now.isoformat()
        self._save()
        return chosen

    def dequeue_by_priority(self, platform: Optional[PlatformType | str] = None) -> Optional[ContentQueueItem]:
        return self.dequeue(platform=platform)

    def ack(self, item_id: str, post_id: Optional[str] = None) -> bool:
        item = self._items.get(item_id)
        if item is None:
            return False
        item.status = QUEUE_STATUS_DONE
        item.updated_at = datetime.now(timezone.utc).isoformat()
        if post_id:
            item.post_id = post_id
        self._save()
        self._logger.info("Acked | id=%s | post_id=%s", item_id, post_id or "?")
        return True

    def nack(
        self,
        item_id: str,
        error: str,
        requeue: bool = True,
        send_to_dlq: bool = False,
    ) -> bool:
        item = self._items.get(item_id)
        if item is None:
            return False
        item.retry_count += 1
        item.error = error
        item.updated_at = datetime.now(timezone.utc).isoformat()

        if send_to_dlq or (item.retry_count >= item.max_retries and requeue):
            item.status = QUEUE_STATUS_DLQ
            self._send_to_dlq(item)
            self._logger.error(
                "Nack (DLQ) | id=%s | retries=%d/%d | error=%s",
                item_id,
                item.retry_count,
                item.max_retries,
                error,
            )
        elif item.retry_count >= item.max_retries:
            item.status = QUEUE_STATUS_FAILED
            self._send_to_dlq(item)
            self._logger.error(
                "Nack (exhausted) | id=%s | retries=%d/%d | error=%s",
                item_id,
                item.retry_count,
                item.max_retries,
                error,
            )
        elif requeue:
            item.status = QUEUE_STATUS_PENDING
            self._logger.warning(
                "Nack (requeue) | id=%s | retry=%d/%d | error=%s",
                item_id,
                item.retry_count,
                item.max_retries,
                error,
            )
        else:
            item.status = QUEUE_STATUS_FAILED
            self._logger.error(
                "Nack (discard) | id=%s | error=%s",
                item_id,
                error,
            )

        self._save()
        return True

    def _send_to_dlq(self, item: ContentQueueItem) -> None:
        if self._dlq_backend is None:
            return
        self._dlq_items[item.item_id] = item
        try:
            data = {iid: it.to_dict() for iid, it in self._dlq_items.items()}
            self._dlq_backend.write(data)
        except Exception as e:
            self._logger.error("Failed to write DLQ | error=%s", e)

    def dlq_count(self) -> int:
        return len(self._dlq_items)

    def dlq_items(self) -> list[ContentQueueItem]:
        return list(self._dlq_items.values())

    def replay_dlq(self, item_id: str) -> bool:
        item = self._dlq_items.pop(item_id)
        if item is None:
            return False
        item.status = QUEUE_STATUS_PENDING
        item.retry_count = 0
        item.error = None
        item.updated_at = datetime.now(timezone.utc).isoformat()
        self._items[item.item_id] = item
        self._save()

        if self._dlq_backend:
            try:
                data = {iid: it.to_dict() for iid, it in self._dlq_items.items()}
                self._dlq_backend.write(data)
            except Exception as e:
                self._logger.error("Failed to update DLQ | error=%s", e)

        self._logger.info("Replayed from DLQ | id=%s", item_id)
        return True

    def replay_all_dlq(self) -> int:
        item_ids = list(self._dlq_items.keys())
        replayed = 0
        for item_id in item_ids:
            if self.replay_dlq(item_id):
                replayed += 1
        if replayed:
            self._logger.info("Replayed %d items from DLQ", replayed)
        return replayed

    def pending_count(self, platform: Optional[PlatformType | str] = None) -> int:
        platform_str = platform.value if isinstance(platform, PlatformType) else platform
        return sum(
            1 for item in self._items.values()
            if item.status == QUEUE_STATUS_PENDING
            and (platform_str is None or item.platform == platform_str)
        )

    def scheduled_count(self, platform: Optional[PlatformType | str] = None) -> int:
        platform_str = platform.value if isinstance(platform, PlatformType) else platform
        return sum(
            1 for item in self._items.values()
            if item.status == QUEUE_STATUS_SCHEDULED
            and (platform_str is None or item.platform == platform_str)
        )

    def failed_count(self, platform: Optional[PlatformType | str] = None) -> int:
        platform_str = platform.value if isinstance(platform, PlatformType) else platform
        return sum(
            1 for item in self._items.values()
            if item.status == QUEUE_STATUS_FAILED
            and (platform_str is None or item.platform == platform_str)
        )

    def all_items(self) -> list[ContentQueueItem]:
        return list(self._items.values())

    def clear_done(self) -> int:
        done_ids = [
            item_id for item_id, item in self._items.items()
            if item.status == QUEUE_STATUS_DONE
        ]
        for item_id in done_ids:
            del self._items[item_id]
        self._save()
        if done_ids:
            self._logger.info("Cleared %d done items", len(done_ids))
        return len(done_ids)

    def _flush_expired(self) -> None:
        expired_ids = [
            item_id for item_id, item in self._items.items()
            if item.is_expired and item.status in {
                QUEUE_STATUS_PENDING, QUEUE_STATUS_SCHEDULED, QUEUE_STATUS_IN_PROGRESS,
            }
        ]
        for item_id in expired_ids:
            item = self._items[item_id]
            item.status = QUEUE_STATUS_FAILED
            item.error = "TTL expired"
            item.updated_at = datetime.now(timezone.utc).isoformat()
            self._send_to_dlq(item)
            self._logger.warning("Expired (TTL) | id=%s", item_id)
        if expired_ids:
            self._save()

    def _release_scheduled(self) -> None:
        now = datetime.now(timezone.utc)
        changed = False
        for item in self._items.values():
            if item.status != QUEUE_STATUS_SCHEDULED:
                continue
            if not item.is_scheduled:
                item.status = QUEUE_STATUS_PENDING
                item.updated_at = now.isoformat()
                changed = True
                self._logger.debug("Released scheduled | id=%s", item.item_id)
        if changed:
            self._save()

    def _load(self) -> None:
        try:
            data = self._backend.read()
            for item_id, item_data in data.items():
                self._items[item_id] = ContentQueueItem.from_dict(item_data)
        except Exception as e:
            self._logger.warning("Failed to load queue | error=%s", e)

        if self._dlq_backend:
            try:
                dlq_data = self._dlq_backend.read()
                for item_id, item_data in dlq_data.items():
                    self._dlq_items[item_id] = ContentQueueItem.from_dict(item_data)
            except Exception as e:
                self._logger.warning("Failed to load DLQ | error=%s", e)

    def _save(self) -> None:
        try:
            data = {item_id: item.to_dict() for item_id, item in self._items.items()}
            self._backend.write(data)
        except Exception as e:
            self._logger.error("Failed to save queue | error=%s", e)
