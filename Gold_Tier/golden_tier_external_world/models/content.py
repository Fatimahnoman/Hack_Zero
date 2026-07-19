from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime

from golden_tier_external_world.config.enums import PlatformType, ContentCategory
from golden_tier_external_world.models.platform import PlatformAccount


@dataclass(frozen=True)
class ContentItem:
    content_id: str
    platform: PlatformType
    content_type: ContentCategory
    text: str
    media_urls: tuple[str, ...] = field(default_factory=tuple)
    author: Optional[PlatformAccount] = None
    created_at: Optional[datetime] = None
    url: Optional[str] = None
    parent_id: Optional[str] = None
    language: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.text and not self.media_urls
