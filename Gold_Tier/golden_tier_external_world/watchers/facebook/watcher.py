import time
import logging
from typing import Optional
from datetime import datetime

from golden_tier_external_world.watchers.base import BaseWatcher
from golden_tier_external_world.events.models import (
    BaseEvent,
    MessageEvent,
    CommentEvent,
    MentionEvent,
)
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.config.enums import ContentCategory, WatcherState


class FacebookWatcher(BaseWatcher):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._page_id: Optional[str] = None
        self._logged_in: bool = False

    def authenticate(self) -> bool:
        self._logger.info("Facebook authenticating with saved session")
        self._logged_in = True
        return True

    def poll(self) -> list[BaseEvent]:
        if not self._logged_in:
            self.authenticate()

        events: list[BaseEvent] = []

        try:
            fb_events = self._fetch_messages()
            events.extend(fb_events)

            notif_events = self._fetch_notifications()
            events.extend(notif_events)

        except Exception:
            self._logger.exception("Facebook poll failed")
            self._state = WatcherState.ERROR

        return events

    def _fetch_messages(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_messages = self._scrape_messenger()

        for msg in raw_messages:
            event_id = f"fb_msg_{msg.get('id', '')}"
            if self._storage.is_processed(event_id):
                continue

            sender = PlatformAccount(
                platform=self.platform,
                account_id=msg.get("sender_id", ""),
                display_name=msg.get("sender_name", "Unknown"),
                username=msg.get("sender_name", "unknown"),
            )

            content = ContentItem(
                content_id=msg.get("id", ""),
                platform=self.platform,
                content_type=ContentCategory.TEXT,
                text=msg.get("text", ""),
                created_at=datetime.fromisoformat(msg["timestamp"]) if "timestamp" in msg else None,
            )

            event = MessageEvent(
                event_id=event_id,
                platform=self.platform,
                timestamp=datetime.utcnow(),
                sender=sender,
                content=content,
                conversation_id=msg.get("conversation_id", ""),
                is_group=msg.get("is_group", False),
                raw_data=msg,
            )
            events.append(event)
            self._storage.mark_processed(event_id)

        return events

    def _fetch_notifications(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_notifs = self._scrape_notifications()

        for notif in raw_notifs:
            event_id = f"fb_notif_{hash(notif.get('text', ''))}"
            if self._storage.is_processed(event_id):
                continue

            author = PlatformAccount(
                platform=self.platform,
                account_id=notif.get("actor_id", ""),
                display_name=notif.get("actor_name", "Unknown"),
                username=notif.get("actor_name", "unknown"),
            )

            content = ContentItem(
                content_id=event_id,
                platform=self.platform,
                content_type=ContentCategory.TEXT,
                text=notif.get("text", ""),
            )

            if "comment" in notif.get("type", ""):
                event: BaseEvent = CommentEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.utcnow(),
                    author=author,
                    content=content,
                    parent_post_id=notif.get("post_id", ""),
                    parent_post_url=notif.get("post_url"),
                    raw_data=notif,
                )
            else:
                event = MentionEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.utcnow(),
                    mentioned_by=author,
                    content=content,
                    source_url=notif.get("source_url"),
                    raw_data=notif,
                )

            events.append(event)
            self._storage.mark_processed(event_id)

        return events

    def _scrape_messenger(self) -> list[dict]:
        return []

    def _scrape_notifications(self) -> list[dict]:
        return []
