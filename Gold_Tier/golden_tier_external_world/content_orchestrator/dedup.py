from __future__ import annotations

from typing import Optional
from hashlib import sha256
import logging

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.events.models import BaseEvent
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault


class ContentDedup:
    def __init__(self, seen_vault: SeenVault) -> None:
        self._vault = seen_vault
        self._logger = logging.getLogger(self.__class__.__name__)

    def is_posted(self, event: BaseEvent, platform: PlatformType) -> bool:
        dedup_key = self._make_key(event, platform)
        seen = self._vault.is_seen(dedup_key)
        if seen:
            self._logger.debug(
                "Already posted | event_id=%s | platform=%s | key=%s",
                event.event_id,
                platform.value,
                dedup_key,
            )
        return seen

    def is_content_posted(
        self,
        event: BaseEvent,
        platform: PlatformType,
        text: str,
    ) -> bool:
        dedup_key = self._make_content_key(event, platform, text)
        seen = self._vault.is_seen(dedup_key)
        if seen:
            self._logger.debug(
                "Duplicate content | event_id=%s | platform=%s | len=%d",
                event.event_id,
                platform.value,
                len(text),
            )
        return seen

    def mark_posted(
        self,
        event: BaseEvent,
        platform: PlatformType,
        post_id: Optional[str] = None,
        text: Optional[str] = None,
    ) -> None:
        dedup_key = self._make_key(event, platform)
        metadata: dict[str, str] = {}
        if post_id:
            metadata["post_id"] = post_id

        self._vault.mark_seen(dedup_key, metadata=metadata)

        if text:
            content_key = self._make_content_key(event, platform, text)
            self._vault.mark_seen(content_key, metadata={
                "event_id": event.event_id,
                "post_id": post_id or "",
            })

        self._logger.info(
            "Marked posted | event_id=%s | platform=%s | post_id=%s",
            event.event_id,
            platform.value,
            post_id or "pending",
        )

    def mark_posted_batch(
        self,
        event: BaseEvent,
        platforms: list[PlatformType],
        post_ids: Optional[dict[PlatformType, str]] = None,
        texts: Optional[dict[PlatformType, str]] = None,
    ) -> None:
        for platform in platforms:
            post_id = post_ids.get(platform) if post_ids else None
            text = texts.get(platform) if texts else None
            self.mark_posted(event, platform, post_id=post_id, text=text)

    def _make_key(self, event: BaseEvent, platform: PlatformType) -> str:
        content_hash = sha256(
            f"{event.event_id}:{platform.value}:{event.event_type.name}".encode()
        ).hexdigest()[:16]
        return f"content:{content_hash}"

    def _make_content_key(
        self,
        event: BaseEvent,
        platform: PlatformType,
        text: str,
    ) -> str:
        content_hash = sha256(
            f"{text}:{platform.value}:{event.event_type.name}".encode()
        ).hexdigest()[:16]
        return f"content_hash:{content_hash}"

    def clear(self) -> None:
        self._vault.clear()
        self._logger.info("Content dedup vault cleared")
