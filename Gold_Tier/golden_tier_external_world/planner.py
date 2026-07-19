import logging
from typing import Any, Optional

from golden_tier_external_world.config.enums import EventType
from golden_tier_external_world.events.models import BaseEvent
from golden_tier_external_world.events.bus import EventBus, Priority
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.content_orchestrator.engine import ContentEngine


_IGNORE_EVENT_TYPES = {
    EventType.LIKE,
    EventType.PROFILE_VIEW,
    EventType.FOLLOW,
    EventType.NOTIFICATION,
    EventType.UNKNOWN,
}


class Planner:
    def __init__(
        self,
        storage: StorageInterface,
        event_bus: EventBus,
        metrics: MetricsCollector,
        content_engine: Optional[ContentEngine] = None,
        ignore_types: Optional[set[EventType]] = None,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._metrics = metrics
        self._content_engine = content_engine
        self._ignore_types = ignore_types or _IGNORE_EVENT_TYPES
        self._logger = logging.getLogger(self.__class__.__name__)
        self._subscriptions: list[object] = []

    def register(self) -> None:
        for et in EventType:
            sub = self._event_bus.subscribe(
                event_type=et,
                handler=self._on_event,
                priority=Priority.MEDIUM,
                name=f"Planner:{et.name}",
            )
            self._subscriptions.append(sub)
        self._logger.info(
            "Planner registered for %d event types",
            len(self._subscriptions),
        )

    def _on_event(self, event: BaseEvent) -> dict[str, Any]:
        self._logger.info(
            "Event received | id=%s | type=%s | platform=%s",
            event.event_id,
            event.event_type.name,
            event.platform.value,
        )
        self._metrics.record_published(event)
        self._metrics.record_handled(event, self.__class__.__name__)

        if event.event_type in self._ignore_types:
            self._logger.debug("Ignored event type | type=%s", event.event_type.name)
            return {"status": "ignored", "event_id": event.event_id}

        result = self._route(event)

        if self._content_engine:
            try:
                content_result = self._content_engine.process_event_sync(event)
                result["content"] = content_result

                if content_result.get("status") == "ok":
                    self._logger.info(
                        "Content generated for event | id=%s | platforms=%s",
                        event.event_id,
                        content_result.get("enqueued_platforms", []),
                    )
                elif content_result.get("status") == "skipped":
                    self._logger.debug(
                        "Content skipped | id=%s | reason=%s",
                        event.event_id,
                        content_result.get("reason", "unknown"),
                    )

            except Exception as e:
                self._logger.error("ContentEngine processing failed | error=%s", e)
                self._metrics.record_error(self.__class__.__name__, str(e))
                result["content"] = {"status": "error", "error": str(e)}

        return result

    def _route(self, event: BaseEvent) -> dict[str, Any]:
        self._logger.debug(
            "Routing event %s | type=%s",
            event.event_id,
            event.event_type.name,
        )
        return {
            "status": "received",
            "event_id": event.event_id,
            "event_type": event.event_type.name,
            "platform": event.platform.value,
        }

    def content_callback(self, phase: str, data: dict[str, Any]) -> None:
        self._logger.info(
            "Content lifecycle | phase=%s | data=%s",
            phase,
            data,
        )

    def shutdown(self) -> None:
        for sub in self._subscriptions:
            self._event_bus.unsubscribe(sub)
        self._subscriptions.clear()
        self._logger.info("Planner shut down")
