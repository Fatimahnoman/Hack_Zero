from typing import Optional
from datetime import datetime

from golden_tier_external_world.watchers.base import BaseWatcher
from golden_tier_external_world.events.models import (
    BaseEvent,
    MentionEvent,
    MessageEvent,
    LikeEvent,
    FollowEvent,
)
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.config.enums import ContentCategory, WatcherState


class TwitterWatcher(BaseWatcher):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._user_id: Optional[str] = None
        self._authenticated: bool = False

    def authenticate(self) -> bool:
        self._logger.info("Twitter authenticating with API credentials")
        self._authenticated = True
        return True

    def poll(self) -> list[BaseEvent]:
        if not self._authenticated:
            self.authenticate()

        events: list[BaseEvent] = []

        try:
            mention_events = self._fetch_mentions()
            events.extend(mention_events)

            dm_events = self._fetch_dms()
            events.extend(dm_events)

            engagement_events = self._fetch_engagements()
            events.extend(engagement_events)

        except Exception:
            self._logger.exception("Twitter poll failed")
            self._state = WatcherState.ERROR

        return events

    def _fetch_mentions(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_mentions = self._api_get_mentions()

        for mention in raw_mentions:
            event_id = f"tw_mention_{mention.get('id', '')}"
            if self._storage.is_processed(event_id):
                continue

            author = PlatformAccount(
                platform=self.platform,
                account_id=mention.get("author_id", ""),
                display_name=mention.get("author_name", "Unknown"),
                username=mention.get("author_username", "unknown"),
            )

            content = ContentItem(
                content_id=mention.get("id", ""),
                platform=self.platform,
                content_type=ContentCategory.TEXT,
                text=mention.get("text", ""),
                url=mention.get("url"),
                created_at=(
                    datetime.fromisoformat(mention["created_at"])
                    if "created_at" in mention else None
                ),
            )

            event = MentionEvent(
                event_id=event_id,
                platform=self.platform,
                timestamp=datetime.utcnow(),
                mentioned_by=author,
                content=content,
                source_url=mention.get("url"),
                raw_data=mention,
            )
            events.append(event)
            self._storage.mark_processed(event_id)

        return events

    def _fetch_dms(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_dms = self._api_get_dms()

        for dm in raw_dms:
            event_id = f"tw_dm_{dm.get('id', '')}"
            if self._storage.is_processed(event_id):
                continue

            sender = PlatformAccount(
                platform=self.platform,
                account_id=dm.get("sender_id", ""),
                display_name=dm.get("sender_name", "Unknown"),
                username=dm.get("sender_username", "unknown"),
            )

            content = ContentItem(
                content_id=dm.get("id", ""),
                platform=self.platform,
                content_type=ContentCategory.TEXT,
                text=dm.get("text", ""),
            )

            event = MessageEvent(
                event_id=event_id,
                platform=self.platform,
                timestamp=datetime.utcnow(),
                sender=sender,
                content=content,
                conversation_id=dm.get("conversation_id", ""),
                raw_data=dm,
            )
            events.append(event)
            self._storage.mark_processed(event_id)

        return events

    def _fetch_engagements(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_engagements = self._api_get_engagements()

        for eng in raw_engagements:
            event_id = f"tw_eng_{eng.get('id', '')}"
            if self._storage.is_processed(event_id):
                continue

            actor = PlatformAccount(
                platform=self.platform,
                account_id=eng.get("actor_id", ""),
                display_name=eng.get("actor_name", "Unknown"),
                username=eng.get("actor_username", "unknown"),
            )

            if eng.get("type") == "like":
                event: BaseEvent = LikeEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.utcnow(),
                    actor=actor,
                    target_content_id=eng.get("tweet_id", ""),
                    target_url=eng.get("tweet_url"),
                    raw_data=eng,
                )
            elif eng.get("type") == "follow":
                event = FollowEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.utcnow(),
                    follower=actor,
                    followed_account_id=eng.get("target_id", ""),
                    raw_data=eng,
                )
            else:
                continue

            events.append(event)
            self._storage.mark_processed(event_id)

        return events

    def _api_get_mentions(self) -> list[dict]:
        return []

    def _api_get_dms(self) -> list[dict]:
        return []

    def _api_get_engagements(self) -> list[dict]:
        return []
