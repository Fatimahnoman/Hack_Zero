from typing import Optional
from datetime import datetime, timezone
from pathlib import Path

from golden_tier_external_world.watchers.base import BaseWatcher
from golden_tier_external_world.events.models import (
    BaseEvent,
    MessageEvent,
    CommentEvent,
    MentionEvent,
    ConnectionRequestEvent,
    ProfileViewEvent,
    NotificationEvent,
)
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.config.enums import ContentCategory, WatcherState
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.events.bus import EventBus


class LinkedInWatcher(BaseWatcher):
    def __init__(
        self,
        config: WatcherConfig,
        storage: StorageInterface,
        event_bus: EventBus,
        seen_vault: Optional[SeenVault] = None,
        vault_path: Optional[Path] = None,
    ) -> None:
        super().__init__(config=config, storage=storage, event_bus=event_bus)
        self._session_valid: bool = False
        self._profile_id: Optional[str] = None

        if seen_vault is not None:
            self._seen = seen_vault
        elif vault_path is not None:
            backend = JsonBackend(vault_path / "linkedin_seen.json")
            self._seen = SeenVault(backend)
        else:
            backend = JsonBackend(
                Path("vault") / "linkedin_seen.json",
                auto_create=True,
            )
            self._seen = SeenVault(backend)

    def authenticate(self) -> bool:
        self._logger.info("LinkedIn authenticating with saved session")
        result = self._validate_session()
        self._session_valid = result
        return result

    def _validate_session(self) -> bool:
        return False

    def poll(self) -> list[BaseEvent]:
        if not self._session_valid:
            self._logger.warning("Session not valid, attempting re-auth")
            if not self.authenticate():
                self._state = WatcherState.ERROR
                return []

        events: list[BaseEvent] = []

        try:
            msg_events = self._fetch_messages()
            events.extend(msg_events)

            req_events = self._fetch_connection_requests()
            events.extend(req_events)

            notif_events = self._fetch_notifications()
            events.extend(notif_events)

        except Exception:
            self._logger.exception("LinkedIn poll failed")
            self._state = WatcherState.ERROR

        return events

    def _fetch_messages(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_messages = self._scrape_messages()

        for msg in raw_messages:
            dedup_id = f"linkedin_msg_{msg.get('conversation_id', '')}_{msg.get('id', '')}"

            if self._seen.is_seen(dedup_id):
                continue

            sender = PlatformAccount(
                platform=self.platform,
                account_id=msg.get("sender_id", ""),
                display_name=msg.get("sender_name", "Unknown"),
                username=msg.get("sender_urn", ""),
                profile_url=msg.get("sender_profile_url"),
                metadata={
                    "headline": msg.get("sender_headline", ""),
                    "company": msg.get("sender_company", ""),
                },
            )

            content = ContentItem(
                content_id=msg.get("id", ""),
                platform=self.platform,
                content_type=ContentCategory.TEXT,
                text=msg.get("text", ""),
                created_at=(
                    datetime.fromisoformat(msg["timestamp"])
                    if "timestamp" in msg else None
                ),
                metadata={"conversation_url": msg.get("conversation_url", "")},
            )

            event = MessageEvent(
                event_id=dedup_id,
                platform=self.platform,
                timestamp=datetime.now(timezone.utc),
                sender=sender,
                content=content,
                conversation_id=msg.get("conversation_id", ""),
                is_group=msg.get("is_group", False),
                raw_data=msg,
            )
            events.append(event)
            self._seen.mark_seen(dedup_id)

        return events

    def _fetch_connection_requests(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_requests = self._scrape_connection_requests()

        for req in raw_requests:
            dedup_id = f"linkedin_connect_{req.get('invitation_id', '')}"

            if self._seen.is_seen(dedup_id):
                continue

            sender = PlatformAccount(
                platform=self.platform,
                account_id=req.get("sender_id", ""),
                display_name=req.get("sender_name", "Unknown"),
                username=req.get("sender_urn", ""),
                profile_url=req.get("sender_profile_url"),
                metadata={
                    "headline": req.get("sender_headline", ""),
                    "connection_degree": req.get("connection_degree", ""),
                },
            )

            event = ConnectionRequestEvent(
                event_id=dedup_id,
                platform=self.platform,
                timestamp=datetime.now(timezone.utc),
                sender=sender,
                message=req.get("message"),
                raw_data=req,
            )
            events.append(event)
            self._seen.mark_seen(dedup_id)

        return events

    def _fetch_notifications(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_notifs = self._scrape_notifications()

        for notif in raw_notifs:
            notif_type = notif.get("type", "")
            dedup_id = f"linkedin_notif_{notif.get('id', '')}"

            if self._seen.is_seen(dedup_id):
                continue

            actor = PlatformAccount(
                platform=self.platform,
                account_id=notif.get("actor_id", ""),
                display_name=notif.get("actor_name", "Unknown"),
                username=notif.get("actor_urn", ""),
                profile_url=notif.get("actor_profile_url"),
                metadata={
                    "headline": notif.get("actor_headline", ""),
                },
            )

            event: BaseEvent

            if notif_type == "post_mention":
                content = ContentItem(
                    content_id=notif.get("post_id", ""),
                    platform=self.platform,
                    content_type=ContentCategory.TEXT,
                    text=notif.get("text", ""),
                    url=notif.get("post_url"),
                    created_at=(
                        datetime.fromisoformat(notif["timestamp"])
                        if "timestamp" in notif else None
                    ),
                )
                event = MentionEvent(
                    event_id=dedup_id,
                    platform=self.platform,
                    timestamp=datetime.now(timezone.utc),
                    mentioned_by=actor,
                    content=content,
                    source_url=notif.get("post_url"),
                    raw_data=notif,
                )

            elif notif_type == "comment_on_post":
                content = ContentItem(
                    content_id=notif.get("comment_id", ""),
                    platform=self.platform,
                    content_type=ContentCategory.TEXT,
                    text=notif.get("text", ""),
                    url=notif.get("post_url"),
                )
                event = CommentEvent(
                    event_id=dedup_id,
                    platform=self.platform,
                    timestamp=datetime.now(timezone.utc),
                    author=actor,
                    content=content,
                    parent_post_id=notif.get("post_id", ""),
                    parent_post_url=notif.get("post_url"),
                    raw_data=notif,
                )

            elif notif_type == "profile_view":
                event = ProfileViewEvent(
                    event_id=dedup_id,
                    platform=self.platform,
                    timestamp=datetime.now(timezone.utc),
                    viewer=actor,
                    headline=notif.get("viewer_headline"),
                    viewed_at=(
                        datetime.fromisoformat(notif["timestamp"])
                        if "timestamp" in notif else None
                    ),
                    raw_data=notif,
                )

            elif notif_type == "like" or notif_type == "reaction":
                content = ContentItem(
                    content_id=notif.get("post_id", ""),
                    platform=self.platform,
                    content_type=ContentCategory.TEXT,
                    text=notif.get("text", ""),
                    url=notif.get("post_url"),
                )
                event = NotificationEvent(
                    event_id=dedup_id,
                    platform=self.platform,
                    timestamp=datetime.now(timezone.utc),
                    notification_type=notif_type,
                    actor=actor,
                    content=content,
                    source_url=notif.get("post_url"),
                    raw_data=notif,
                )

            else:
                content = ContentItem(
                    content_id=notif.get("id", ""),
                    platform=self.platform,
                    content_type=ContentCategory.TEXT,
                    text=notif.get("text", ""),
                )
                event = NotificationEvent(
                    event_id=dedup_id,
                    platform=self.platform,
                    timestamp=datetime.now(timezone.utc),
                    notification_type=notif_type or "unknown",
                    actor=actor if notif.get("actor_id") else None,
                    content=content if notif.get("text") else None,
                    source_url=notif.get("source_url"),
                    raw_data=notif,
                )

            events.append(event)
            self._seen.mark_seen(dedup_id)

        return events

    def _scrape_messages(self) -> list[dict]:
        return []

    def _scrape_connection_requests(self) -> list[dict]:
        return []

    def _scrape_notifications(self) -> list[dict]:
        return []

    @property
    def seen_vault(self) -> SeenVault:
        return self._seen
