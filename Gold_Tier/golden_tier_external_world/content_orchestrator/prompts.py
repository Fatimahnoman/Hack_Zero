from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging
import re

from golden_tier_external_world.config.enums import EventType, PlatformType
from golden_tier_external_world.events.models import BaseEvent, MessageEvent


_DEFAULT_PROMPTS: dict[tuple[EventType, PlatformType], str] = {
    (EventType.MESSAGE, PlatformType.FACEBOOK): (
        "You are replying to a Facebook message from {sender_name}. "
        "Write a warm, friendly response. Keep it under 500 characters.\n"
        "Message: \"{message_text}\"\nResponse:"
    ),
    (EventType.MESSAGE, PlatformType.TWITTER): (
        "You are replying to a DM on Twitter/X from {sender_name}. "
        "Write a concise, friendly reply. Keep it under 280 characters.\n"
        "Message: \"{message_text}\"\nResponse:"
    ),
    (EventType.MESSAGE, PlatformType.INSTAGRAM): (
        "You are replying to an Instagram DM from {sender_name}. "
        "Write a casual, friendly response. Keep it under 500 characters.\n"
        "Message: \"{message_text}\"\nResponse:"
    ),
    (EventType.MESSAGE, PlatformType.LINKEDIN): (
        "You are replying to a LinkedIn message from {sender_name}. "
        "Write a professional, courteous response. Keep it under 500 characters.\n"
        "Message: \"{message_text}\"\nResponse:"
    ),
    (EventType.COMMENT, PlatformType.FACEBOOK): (
        "Someone commented on your Facebook post. "
        "Reply warmly and acknowledge their input. Keep it under 500 characters.\n"
        "Comment: \"{message_text}\"\nResponse:"
    ),
    (EventType.COMMENT, PlatformType.INSTAGRAM): (
        "Someone commented on your Instagram post. "
        "Reply casually and appreciatively. Keep it under 500 characters.\n"
        "Comment: \"{message_text}\"\nResponse:"
    ),
    (EventType.COMMENT, PlatformType.TWITTER): (
        "Someone replied to your tweet. "
        "Reply briefly and thank them. Keep it under 280 characters.\n"
        "Reply: \"{message_text}\"\nResponse:"
    ),
    (EventType.MENTION, PlatformType.TWITTER): (
        "You were mentioned on Twitter/X by {sender_name}. "
        "Acknowledge the mention politely. Keep it under 280 characters.\n"
        "Tweet: \"{message_text}\"\nResponse:"
    ),
    (EventType.MENTION, PlatformType.INSTAGRAM): (
        "You were mentioned in an Instagram story or post by {sender_name}. "
        "Reply with appreciation. Keep it under 500 characters.\n"
        "Content: \"{message_text}\"\nResponse:"
    ),
    (EventType.MENTION, PlatformType.FACEBOOK): (
        "You were mentioned in a Facebook post by {sender_name}. "
        "Thank them and engage. Keep it under 500 characters.\n"
        "Post: \"{message_text}\"\nResponse:"
    ),
}


DEFAULT_PLATFORM_LIMITS: dict[PlatformType, int] = {
    PlatformType.TWITTER: 280,
    PlatformType.FACEBOOK: 63206,
    PlatformType.INSTAGRAM: 2200,
    PlatformType.LINKEDIN: 3000,
}


@dataclass
class PromptTemplate:
    template: str
    event_type: EventType
    platform: PlatformType
    language: str = "en"
    tone: str = "friendly"
    version: int = 1


class PromptManager:
    def __init__(self) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._templates: dict[tuple[EventType, PlatformType, str, str], PromptTemplate] = {}
        self._overrides: dict[tuple[EventType, PlatformType], str] = {}
        self._load_defaults()

    def _load_defaults(self) -> None:
        for (et, pt), template_str in _DEFAULT_PROMPTS.items():
            key = (et, pt, "en", "friendly")
            self._templates[key] = PromptTemplate(
                template=template_str,
                event_type=et,
                platform=pt,
                language="en",
                tone="friendly",
            )

    def register_template(self, template: PromptTemplate) -> None:
        key = (template.event_type, template.platform, template.language, template.tone)
        self._templates[key] = template
        self._logger.info(
            "Registered prompt template | event=%s | platform=%s | lang=%s | tone=%s",
            template.event_type.name,
            template.platform.value,
            template.language,
            template.tone,
        )

    def set_override(
        self,
        event_type: EventType,
        platform: PlatformType,
        prompt: str,
    ) -> None:
        self._overrides[(event_type, platform)] = prompt
        self._logger.info(
            "Prompt override set | event=%s | platform=%s",
            event_type.name,
            platform.value,
        )

    def get_system_prompt(
        self,
        event_type: EventType,
        platform: PlatformType,
        language: str = "en",
        tone: str = "friendly",
    ) -> Optional[str]:
        override = self._overrides.get((event_type, platform))
        if override is not None:
            return override

        key = (event_type, platform, language, tone)
        if key not in self._templates:
            key = (event_type, platform, "en", "friendly")
        if key not in self._templates:
            alt_key = next(
                (k for k in self._templates if k[0] == event_type and k[1] == platform),
                None,
            )
            if alt_key:
                key = alt_key
            else:
                return None
        return self._templates[key].template

    def build_user_content(self, event: BaseEvent) -> str:
        sender_name = "there"
        message_text = ""

        if hasattr(event, "sender") and event.sender:
            sender_name = event.sender.display_name or event.sender.username or "there"
        elif hasattr(event, "author") and event.author:
            sender_name = event.author.display_name or event.author.username or "there"
        elif hasattr(event, "mentioned_by") and event.mentioned_by:
            sender_name = event.mentioned_by.display_name or event.mentioned_by.username or "there"

        if hasattr(event, "content") and event.content:
            message_text = getattr(event.content, "text", "")

        return f"From: {sender_name}\nMessage: {message_text}"

    def format_prompt(
        self,
        event_type: EventType,
        platform: PlatformType,
        event: BaseEvent,
        language: str = "en",
        tone: str = "friendly",
    ) -> Optional[str]:
        template = self.get_system_prompt(event_type, platform, language, tone)
        if template is None:
            return None

        sender_name = "there"
        message_text = ""

        if hasattr(event, "sender") and event.sender:
            sender_name = event.sender.display_name or event.sender.username or "there"
        elif hasattr(event, "author") and event.author:
            sender_name = event.author.display_name or event.author.username or "there"
        elif hasattr(event, "mentioned_by") and event.mentioned_by:
            sender_name = event.mentioned_by.display_name or event.mentioned_by.username or "there"

        if hasattr(event, "content") and event.content:
            message_text = getattr(event.content, "text", "")

        return template.format(
            sender_name=sender_name,
            message_text=message_text,
            platform=platform.value.capitalize(),
        )

    @property
    def registered_count(self) -> int:
        return len(self._templates)
