from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging
import re

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.posters.base import PostContent


DEFAULT_MAX_LENGTH: dict[PlatformType, int] = {
    PlatformType.TWITTER: 280,
    PlatformType.FACEBOOK: 63206,
    PlatformType.INSTAGRAM: 2200,
    PlatformType.LINKEDIN: 3000,
}


_UNSAFE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(hate|violence|abuse|scam|fraud|spam)\b", re.IGNORECASE),
    re.compile(r"(https?://)?(bit\.ly|tinyurl|shorturl)\S+", re.IGNORECASE),
]

_REPEATED_SENTENCE_PATTERN = re.compile(r"([A-Z][^.!?]*[.!?])\s*\1", re.IGNORECASE)

_URL_PATTERN = re.compile(
    r"https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)"
)

_HASHTAG_PATTERN = re.compile(r"#\w+")
_EMOJI_PATTERN = re.compile(
    "[" 
    "\U0001F600-\U0001F64F"  
    "\U0001F300-\U0001F5FF"  
    "\U0001F680-\U0001F6FF"  
    "\U0001F1E0-\U0001F1FF"  
    "\U00002702-\U000027B0"  
    "\U000024C2-\U0001F251"  
    "]+"
)


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ContentValidator:
    def __init__(
        self,
        max_lengths: Optional[dict[PlatformType, int]] = None,
        check_unsafe: bool = True,
        check_duplicate_hashtags: bool = True,
        check_duplicate_emojis: bool = True,
        check_repeated_sentences: bool = True,
        check_urls: bool = True,
        check_media: bool = True,
    ) -> None:
        self._max_lengths = max_lengths or dict(DEFAULT_MAX_LENGTH)
        self._check_unsafe = check_unsafe
        self._check_duplicate_hashtags = check_duplicate_hashtags
        self._check_duplicate_emojis = check_duplicate_emojis
        self._check_repeated_sentences = check_repeated_sentences
        self._check_urls = check_urls
        self._check_media = check_media
        self._logger = logging.getLogger(self.__class__.__name__)

    def set_max_length(self, platform: PlatformType, length: int) -> None:
        self._max_lengths[platform] = length

    def validate(
        self,
        content: PostContent,
        platform: PlatformType,
    ) -> ValidationResult:
        result = ValidationResult()
        text = content.text or ""

        self._check_empty(text, result)
        self._check_max_length(text, platform, result)
        if self._check_unsafe:
            self._check_unsafe_content(text, result)
        if self._check_duplicate_hashtags:
            self._check_duplicate_hashtags_fn(text, result)
        if self._check_duplicate_emojis:
            self._check_duplicate_emojis_fn(text, result)
        if self._check_repeated_sentences:
            self._check_repeated_sentences_fn(text, result)
        if self._check_urls:
            self._check_urls_in_text(text, result)
        if self._check_media:
            self._check_media_paths(content.media_paths, result)

        return result

    def _check_empty(self, text: str, result: ValidationResult) -> None:
        if not text or not text.strip():
            result.valid = False
            result.errors.append("Content text is empty")

    def _check_max_length(
        self,
        text: str,
        platform: PlatformType,
        result: ValidationResult,
    ) -> None:
        max_len = self._max_lengths.get(platform, 500)
        if len(text) > max_len:
            result.valid = False
            result.errors.append(
                f"Text exceeds {platform.value} limit of {max_len} characters "
                f"(got {len(text)})"
            )

    def _check_unsafe_content(self, text: str, result: ValidationResult) -> None:
        for pattern in _UNSAFE_PATTERNS:
            match = pattern.search(text)
            if match:
                result.valid = False
                result.errors.append(
                    f"Content flagged as unsafe (matched: '{match.group()}')"
                )
                return

    def _check_duplicate_hashtags_fn(self, text: str, result: ValidationResult) -> None:
        hashtags = _HASHTAG_PATTERN.findall(text)
        if len(hashtags) != len(set(hashtags)):
            result.valid = False
            result.errors.append("Duplicate hashtags detected")

    def _check_duplicate_emojis_fn(self, text: str, result: ValidationResult) -> None:
        emojis = _EMOJI_PATTERN.findall(text)
        seen: set[str] = set()
        for emoji_block in emojis:
            for char in emoji_block:
                if char in seen:
                    result.valid = False
                    result.errors.append("Duplicate emojis detected")
                    return
                seen.add(char)

    def _check_repeated_sentences_fn(self, text: str, result: ValidationResult) -> None:
        if _REPEATED_SENTENCE_PATTERN.search(text):
            result.warnings.append("Repeated sentences detected")

    def _check_urls_in_text(self, text: str, result: ValidationResult) -> None:
        urls = _URL_PATTERN.findall(text)
        for url_tuple in urls:
            url = url_tuple[0] if isinstance(url_tuple, tuple) else url_tuple
            if not url.startswith(("http://", "https://")):
                result.warnings.append(f"URL missing protocol: {url}")

    def _check_media_paths(
        self,
        media_paths: list[Path],
        result: ValidationResult,
    ) -> None:
        for path in media_paths:
            if not path.exists():
                result.warnings.append(f"Media file not found: {path}")
            ext = path.suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov", ".pdf"}:
                result.warnings.append(f"Unsupported media format: {ext}")
