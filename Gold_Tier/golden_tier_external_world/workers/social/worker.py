from typing import Any

from golden_tier_external_world.config.enums import EventType, PlatformType
from golden_tier_external_world.events.models import BaseEvent, MessageEvent
from golden_tier_external_world.workers.social.base import BaseSocialWorker


class SocialMediaWorker(BaseSocialWorker):
    @property
    def event_type(self) -> EventType:
        return EventType.MESSAGE

    def can_handle(self, event: BaseEvent) -> bool:
        return isinstance(event, MessageEvent) and event.platform in (
            PlatformType.FACEBOOK,
            PlatformType.INSTAGRAM,
            PlatformType.TWITTER,
        )

    def process(self, event: BaseEvent) -> dict[str, Any]:
        self._logger.info(
            "Processing %s event from %s",
            event.event_type.name,
            event.platform.value,
        )

        if not isinstance(event, MessageEvent):
            self._logger.warning("Unsupported event type: %s", type(event).__name__)
            return {"status": "skipped", "reason": "unsupported_event_type"}

        return self._on_event(event)

    def _execute(self, event: MessageEvent) -> dict[str, Any]:
        return {
            "status": "pending",
            "platform": event.platform.value,
            "sender": str(event.sender),
            "conversation_id": event.conversation_id,
            "message_preview": event.content.text[:100] if event.content.text else "",
        }
