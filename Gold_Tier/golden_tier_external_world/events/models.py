from dataclasses import dataclass, field
from typing import Optional, Any, ClassVar
from datetime import datetime, timezone
from uuid import uuid4

from golden_tier_external_world.config.enums import PlatformType, EventType
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem

@dataclass(kw_only=True)
class BaseEvent:
    platform: PlatformType
    timestamp: datetime
    event_id: str = field(default_factory=lambda: uuid4().hex)
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> EventType:
        raise NotImplementedError

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


@dataclass(kw_only=True)
class MessageEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.MESSAGE
    sender: PlatformAccount
    content: ContentItem
    conversation_id: str
    is_group: bool = False


@dataclass(kw_only=True)
class CommentEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.COMMENT
    author: PlatformAccount
    content: ContentItem
    parent_post_id: str
    parent_post_url: Optional[str] = None


@dataclass(kw_only=True)
class MentionEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.MENTION
    mentioned_by: PlatformAccount
    content: ContentItem
    source_url: Optional[str] = None


@dataclass(kw_only=True)
class LikeEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.LIKE
    actor: PlatformAccount
    target_content_id: str
    target_url: Optional[str] = None


@dataclass(kw_only=True)
class FollowEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.FOLLOW
    follower: PlatformAccount
    followed_account_id: str


@dataclass(kw_only=True)
class ConnectionRequestEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.CONNECTION_REQUEST
    sender: PlatformAccount
    message: Optional[str] = None


@dataclass(kw_only=True)
class ShareEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.SHARE
    sharer: PlatformAccount
    content: ContentItem
    shared_to_platform: Optional[PlatformType] = None


@dataclass(kw_only=True)
class ProfileViewEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.PROFILE_VIEW
    viewer: PlatformAccount
    headline: Optional[str] = None
    viewed_at: Optional[datetime] = None


@dataclass(kw_only=True)
class NotificationEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.NOTIFICATION
    notification_type: str
    actor: Optional[PlatformAccount] = None
    content: Optional[ContentItem] = None
    source_url: Optional[str] = None


@dataclass(kw_only=True)
class UnknownEvent(BaseEvent):
    event_type: ClassVar[EventType] = EventType.UNKNOWN
    source: str
