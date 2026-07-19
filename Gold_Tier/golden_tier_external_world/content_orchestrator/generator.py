from __future__ import annotations

from typing import Any, Callable, Optional
import logging
import re

from golden_tier_external_world.config.enums import EventType, PlatformType
from golden_tier_external_world.events.models import BaseEvent, MessageEvent, CommentEvent, MentionEvent
from golden_tier_external_world.posters.base import PostContent
from golden_tier_external_world.content_orchestrator.prompts import PromptManager


AiGeneratorFn = Callable[[BaseEvent, PlatformType], Optional[str]]
RuleFn = Callable[[BaseEvent, PlatformType], Optional[str]]

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful social media assistant. "
    "Generate a brief, friendly response to the user's message. "
    "Keep it under 280 characters for Twitter, "
    "and under 500 characters for Facebook and Instagram."
)


class ContentGenerator:
    def __init__(
        self,
        generator_fn: Optional[AiGeneratorFn] = None,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        openai_api_key: Optional[str] = None,
        openai_model: str = "gpt-4o-mini",
        openai_max_tokens: int = 150,
        prompt_manager: Optional[PromptManager] = None,
        rule_fns: Optional[list[RuleFn]] = None,
        platform_char_limits: Optional[dict[PlatformType, int]] = None,
    ) -> None:
        self._generator_fn = generator_fn
        self._system_prompt = system_prompt
        self._openai_api_key = openai_api_key
        self._openai_model = openai_model
        self._openai_max_tokens = openai_max_tokens
        self._prompt_manager = prompt_manager or PromptManager()
        self._rule_fns = rule_fns or []
        self._platform_char_limits = platform_char_limits or {
            PlatformType.TWITTER: 280,
            PlatformType.FACEBOOK: 500,
            PlatformType.INSTAGRAM: 500,
            PlatformType.LINKEDIN: 500,
        }
        self._logger = logging.getLogger(self.__class__.__name__)

    def add_rule(self, rule_fn: RuleFn) -> None:
        self._rule_fns.append(rule_fn)

    def generate(
        self,
        event: BaseEvent,
        platform: PlatformType,
    ) -> Optional[PostContent]:
        text = self._try_custom_generator(event, platform)
        if text is not None:
            text = self._apply_char_limit(text, platform)
            self._log_generated(platform, event, "custom", len(text))
            return PostContent(text=text)

        text = self._try_openai(event, platform)
        if text is not None:
            text = self._apply_char_limit(text, platform)
            self._log_generated(platform, event, "openai", len(text))
            return PostContent(text=text)

        text = self._try_template(event, platform)
        if text is not None:
            text = self._apply_char_limit(text, platform)
            self._log_generated(platform, event, "template", len(text))
            return PostContent(text=text)

        text = self._try_rules(event, platform)
        if text is not None:
            text = self._apply_char_limit(text, platform)
            self._log_generated(platform, event, "rule", len(text))
            return PostContent(text=text)

        event_id = getattr(event, "event_id", None) or "?"
        self._logger.warning(
            "All generators returned None | platform=%s | event_id=%s",
            platform.value,
            event_id,
        )
        return None

    def _try_custom_generator(self, event: BaseEvent, platform: PlatformType) -> Optional[str]:
        if self._generator_fn is None:
            return None
        try:
            return self._generator_fn(event, platform)
        except Exception as e:
            self._logger.error("Custom generator failed | error=%s", e)
            return None

    def _try_openai(self, event: BaseEvent, platform: PlatformType) -> Optional[str]:
        if not self._openai_api_key:
            return None
        try:
            return self._generate_openai(event, platform)
        except Exception as e:
            self._logger.error("OpenAI generation failed | error=%s", e)
            return None

    def _try_template(self, event: BaseEvent, platform: PlatformType) -> Optional[str]:
        try:
            return self._generate_template(event, platform)
        except Exception as e:
            self._logger.error("Template generation failed | error=%s", e)
            return None

    def _try_rules(self, event: BaseEvent, platform: PlatformType) -> Optional[str]:
        for rule_fn in self._rule_fns:
            try:
                text = rule_fn(event, platform)
                if text is not None:
                    return text
            except Exception as e:
                self._logger.error("Rule engine failed | error=%s", e)
        return None

    def _generate_openai(self, event: BaseEvent, platform: PlatformType) -> Optional[str]:
        from openai import OpenAI
        client = OpenAI(api_key=self._openai_api_key)

        platform_name = platform.value.capitalize()
        char_limit = self._platform_char_limits.get(platform, 500)

        formatted = self._prompt_manager.format_prompt(
            event.event_type, platform, event,
        )
        if formatted:
            system_prompt = self._system_prompt
            user_content = formatted
        else:
            system_prompt = self._system_prompt
            user_content = self._prompt_manager.build_user_content(event)

        response = client.chat.completions.create(
            model=self._openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=self._openai_max_tokens,
            temperature=0.7,
        )

        text = response.choices[0].message.content
        if text and len(text) > char_limit:
            text = text[:char_limit]
        return text

    def _generate_template(self, event: BaseEvent, platform: PlatformType) -> Optional[str]:
        if isinstance(event, MessageEvent):
            sender_name = event.sender.display_name or event.sender.username or "there"
            return self._template_response(sender_name, event.content.text, platform)

        if isinstance(event, CommentEvent):
            return self._template_comment_response(event, platform)

        if isinstance(event, MentionEvent):
            return self._template_mention_response(event, platform)

        if event.event_type == EventType.COMMENT:
            return self._template_comment_response(event, platform)

        if event.event_type == EventType.MENTION:
            return self._template_mention_response(event, platform)

        return None

    def _template_response(self, sender: str, message: Optional[str], platform: PlatformType) -> str:
        char_limit = self._platform_char_limits.get(platform, 500)
        text = f"Thanks for your message, {sender}! We'll get back to you shortly."
        if len(text) > char_limit:
            text = text[:char_limit]
        return text

    def _template_comment_response(self, event: BaseEvent, platform: PlatformType) -> str:
        char_limit = self._platform_char_limits.get(platform, 500)
        text = "Thanks for your comment! Glad to see your engagement."
        if len(text) > char_limit:
            text = text[:char_limit]
        return text

    def _template_mention_response(self, event: BaseEvent, platform: PlatformType) -> str:
        char_limit = self._platform_char_limits.get(platform, 500)
        text = "Thanks for the mention! Appreciate the shoutout."
        if len(text) > char_limit:
            text = text[:char_limit]
        return text

    def _apply_char_limit(self, text: str, platform: PlatformType) -> str:
        limit = self._platform_char_limits.get(platform)
        if limit and len(text) > limit:
            return text[:limit]
        return text

    def _log_generated(
        self,
        platform: PlatformType,
        event: BaseEvent,
        source: str,
        length: int,
    ) -> None:
        event_id = getattr(event, "event_id", None) or getattr(event, "event_type", None) or "?"
        self._logger.info(
            "Content generated | platform=%s | event_id=%s | source=%s | text_len=%d",
            platform.value,
            event_id,
            source,
            length,
        )
