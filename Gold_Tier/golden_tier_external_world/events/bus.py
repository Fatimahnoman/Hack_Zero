from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from queue import PriorityQueue
from threading import Thread, Event as ThreadEvent, RLock
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
)
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import asyncio
import inspect
import logging
import time
import uuid

from golden_tier_external_world.config.enums import EventType
from golden_tier_external_world.events.models import BaseEvent

EventHandler = Callable[[BaseEvent], Any]
AsyncEventHandler = Callable[[BaseEvent], Awaitable[Any]]


class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


@dataclass(order=True)
class _QueueItem:
    priority: int
    timestamp: float
    envelope: "EventEnvelope" = field(compare=False)


@dataclass
class EventEnvelope:
    event: BaseEvent
    priority: Priority = Priority.MEDIUM
    retry_count: int = 0
    max_retries: int = 3
    published_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    correlation_id: str = field(
        default_factory=lambda: uuid.uuid4().hex
    )
    errors: list[str] = field(default_factory=list)

    @property
    def event_id(self) -> str:
        return self.event.event_id

    @property
    def failed(self) -> bool:
        return self.retry_count >= self.max_retries


@dataclass
class SubscriptionInfo:
    handler: EventHandler | AsyncEventHandler
    event_type: EventType
    priority: Priority = Priority.MEDIUM
    name: Optional[str] = None
    filters: list[Callable[[BaseEvent], bool]] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def matches(self, event: BaseEvent) -> bool:
        if not self.filters:
            return True
        return all(f(event) for f in self.filters)

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.handler)


@dataclass
class DeadLetterEntry:
    envelope: EventEnvelope
    reason: str
    failed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def event_id(self) -> str:
        return self.envelope.event_id


class EventQueue:
    def __init__(self, maxsize: int = 0) -> None:
        self._queue: PriorityQueue[_QueueItem] = PriorityQueue(maxsize=maxsize)
        self._count = 0

    def put(
        self,
        envelope: EventEnvelope,
        priority: Optional[Priority] = None,
    ) -> None:
        p = (priority or envelope.priority).value
        item = _QueueItem(
            priority=p,
            timestamp=time.time(),
            envelope=envelope,
        )
        self._queue.put(item)
        self._count += 1

    def get(self, timeout: float = 1.0) -> Optional[EventEnvelope]:
        try:
            item = self._queue.get(timeout=timeout)
            self._count -= 1
            return item.envelope
        except Exception:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def total_enqueued(self) -> int:
        return self._count


class EventBus(ABC):
    @abstractmethod
    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler | AsyncEventHandler,
        priority: Priority = Priority.MEDIUM,
        name: Optional[str] = None,
        filters: Optional[list[Callable[[BaseEvent], bool]]] = None,
    ) -> SubscriptionInfo: ...

    @abstractmethod
    def unsubscribe(self, subscription: SubscriptionInfo) -> None: ...

    @abstractmethod
    def publish(
        self,
        event: BaseEvent,
        priority: Priority = Priority.MEDIUM,
        max_retries: int = 3,
    ) -> str: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def dead_letter_queue(self) -> list[DeadLetterEntry]: ...

    @abstractmethod
    def replay(self, entry: DeadLetterEntry) -> None: ...


class ProductionEventBus(EventBus):
    def __init__(
        self,
        max_workers: int = 4,
        queue_maxsize: int = 0,
        handler_timeout: float = 30.0,
        enable_async: bool = True,
    ) -> None:
        self._subscriptions: dict[EventType, list[SubscriptionInfo]] = (
            defaultdict(list)
        )
        self._lock = RLock()
        self._queue = EventQueue(maxsize=queue_maxsize)
        self._dlq: list[DeadLetterEntry] = []
        self._dlq_lock = RLock()

        self._running = False
        self._stop_event = ThreadEvent()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="evtbus",
        )
        self._processor_thread: Optional[Thread] = None
        self._handler_timeout = handler_timeout
        self._enable_async = enable_async
        self._logger = logging.getLogger("EventBus")

        self._publish_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._dlq_count = 0

    # ── Subscription ──────────────────────────────────────────

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler | AsyncEventHandler,
        priority: Priority = Priority.MEDIUM,
        name: Optional[str] = None,
        filters: Optional[list[Callable[[BaseEvent], bool]]] = None,
    ) -> SubscriptionInfo:
        sub = SubscriptionInfo(
            handler=handler,
            event_type=event_type,
            priority=priority,
            name=name or getattr(handler, "__name__", str(handler)),
            filters=filters or [],
        )
        with self._lock:
            self._subscriptions[event_type].append(sub)
        self._logger.info(
            "Subscribed | handler=%s | event_type=%s | priority=%s",
            sub.name,
            sub.event_type.name,
            sub.priority.name,
        )
        return sub

    def unsubscribe(self, subscription: SubscriptionInfo) -> None:
        with self._lock:
            subs = self._subscriptions.get(subscription.event_type, [])
            self._subscriptions[subscription.event_type] = [
                s for s in subs if s is not subscription
            ]
        self._logger.info(
            "Unsubscribed | handler=%s | event_type=%s",
            subscription.name,
            subscription.event_type.name,
        )

    def unsubscribe_all(self, event_type: Optional[EventType] = None) -> None:
        with self._lock:
            if event_type:
                self._subscriptions[event_type].clear()
            else:
                self._subscriptions.clear()
        self._logger.info("All subscriptions removed")

    # ── Publish ───────────────────────────────────────────────

    def publish(
        self,
        event: BaseEvent,
        priority: Priority = Priority.MEDIUM,
        max_retries: int = 3,
    ) -> str:
        envelope = EventEnvelope(
            event=event,
            priority=priority,
            max_retries=max_retries,
        )
        self._publish_count += 1
        self._logger.info(
            "Publish | id=%s | type=%s | platform=%s | priority=%s",
            envelope.event_id,
            event.event_type.name if hasattr(event, "event_type") else "?",
            event.platform.value,
            priority.name,
        )

        if self._running:
            self._queue.put(envelope, priority=priority)
        else:
            self._dispatch_sync(envelope)

        return envelope.correlation_id

    def publish_sync(
        self,
        event: BaseEvent,
        priority: Priority = Priority.MEDIUM,
        max_retries: int = 3,
    ) -> str:
        envelope = EventEnvelope(
            event=event,
            priority=priority,
            max_retries=max_retries,
        )
        self._publish_count += 1
        self._dispatch_sync(envelope)
        return envelope.correlation_id

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._processor_thread = Thread(
            target=self._process_loop,
            name="evtbus-processor",
            daemon=True,
        )
        self._processor_thread.start()
        self._logger.info(
            "EventBus started | workers=%d | queue=%s",
            self._executor._max_workers,
            "unbounded" if self._queue._queue.maxsize == 0 else str(self._queue._queue.maxsize),
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        self._stop_event.set()

        if self._processor_thread and self._processor_thread.is_alive():
            self._processor_thread.join(timeout=timeout)

        self._executor.shutdown(wait=False)
        self._logger.info(
            "EventBus stopped | published=%d | success=%d | failed=%d | dlq=%d",
            self._publish_count,
            self._success_count,
            self._failure_count,
            self._dlq_count,
        )

    # ── Processing ────────────────────────────────────────────

    def _process_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            envelope = self._queue.get(timeout=0.5)
            if envelope is None:
                continue
            self._executor.submit(self._dispatch_with_retry, envelope)

    def _dispatch_with_retry(self, envelope: EventEnvelope) -> None:
        while not envelope.failed:
            try:
                self._dispatch_to_handlers(envelope)
                self._success_count += 1
                return
            except Exception as e:
                envelope.retry_count += 1
                error_msg = f"{type(e).__name__}: {e}"
                envelope.errors.append(error_msg)
                self._logger.warning(
                    "Handler failed | id=%s | attempt=%d/%d | error=%s",
                    envelope.event_id,
                    envelope.retry_count,
                    envelope.max_retries,
                    error_msg,
                )
                if not envelope.failed:
                    backoff = min(30, 2.0 ** envelope.retry_count)
                    time.sleep(backoff)

        self._failure_count += 1
        self._send_to_dlq(envelope)

    def _dispatch_to_handlers(self, envelope: EventEnvelope) -> None:
        with self._lock:
            subs = list(
                self._subscriptions.get(envelope.event.event_type, [])
            )

        if not subs:
            self._logger.debug(
                "No handlers for event type %s",
                envelope.event.event_type.name,
            )
            return

        for sub in subs:
            if not sub.matches(envelope.event):
                continue
            try:
                if sub.is_async and self._enable_async:
                    asyncio.run(sub.handler(envelope.event))
                else:
                    sub.handler(envelope.event)
            except Exception as e:
                self._logger.error(
                    "Handler error | handler=%s | event_id=%s | error=%s",
                    sub.name,
                    envelope.event_id,
                    e,
                )
                raise

    def _dispatch_sync(self, envelope: EventEnvelope) -> None:
        try:
            self._dispatch_to_handlers(envelope)
        except Exception as e:
            self._logger.error(
                "Sync dispatch failed | id=%s | error=%s",
                envelope.event_id,
                e,
            )

    # ── Dead Letter Queue ─────────────────────────────────────

    def _send_to_dlq(self, envelope: EventEnvelope) -> None:
        reason = envelope.errors[-1] if envelope.errors else "max_retries_exceeded"
        entry = DeadLetterEntry(envelope=envelope, reason=reason)
        with self._dlq_lock:
            self._dlq.append(entry)
            self._dlq_count += 1
        self._logger.error(
            "DLQ | id=%s | type=%s | retries=%d | reason=%s",
            envelope.event_id,
            envelope.event.event_type.name,
            envelope.retry_count,
            reason,
        )

    def dead_letter_queue(self) -> list[DeadLetterEntry]:
        with self._dlq_lock:
            return list(self._dlq)

    def replay(self, entry: DeadLetterEntry) -> None:
        with self._dlq_lock:
            if entry in self._dlq:
                self._dlq.remove(entry)
                self._dlq_count -= 1

        new_envelope = EventEnvelope(
            event=entry.envelope.event,
            priority=entry.envelope.priority,
            max_retries=entry.envelope.max_retries,
            correlation_id=entry.envelope.correlation_id,
        )
        self._logger.info(
            "Replaying DLQ entry | id=%s", entry.event_id,
        )
        if self._running:
            self._queue.put(new_envelope)
        else:
            self._dispatch_sync(new_envelope)

    def replay_all(self) -> int:
        entries = self.dead_letter_queue()
        for entry in entries:
            self.replay(entry)
        return len(entries)

    def clear_dlq(self) -> None:
        with self._dlq_lock:
            self._dlq.clear()
            self._dlq_count = 0
        self._logger.info("Dead letter queue cleared")

    # ── Stats ─────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            sub_count = sum(len(v) for v in self._subscriptions.values())
        return {
            "published": self._publish_count,
            "success": self._success_count,
            "failed": self._failure_count,
            "dlq": self._dlq_count,
            "queue_depth": self._queue.qsize(),
            "subscriptions": sub_count,
            "running": self._running,
            "max_workers": self._executor._max_workers,
        }

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subscriptions.values())

    def __enter__(self) -> ProductionEventBus:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()


class LocalEventBus(ProductionEventBus):
    def __init__(self) -> None:
        super().__init__(max_workers=1, enable_async=False)
