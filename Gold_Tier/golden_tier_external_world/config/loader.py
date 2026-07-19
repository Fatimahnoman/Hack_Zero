from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import json
import logging
import os

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.config.settings import (
    AppSettings,
    ContentConfig,
    Credentials,
    StorageConfig,
    WatcherConfig,
)
from golden_tier_external_world.config.secrets import get_secret, interpolate_env_vars


_LOGGER = logging.getLogger("config.loader")


def load_settings(
    vault_root: Path,
    config_path: Optional[Path] = None,
    env_prefix: str = "GT_",
) -> AppSettings:
    settings = AppSettings.defaults(vault_root)
    settings.vault_path = vault_root

    if config_path and config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            _apply_config_dict(settings, data)
        except Exception as e:
            _LOGGER.warning(
                "Failed to load config file | path=%s | error=%s",
                config_path, e,
            )

    _apply_env_overrides(settings, env_prefix)

    if settings.storage:
        settings.storage.vault_path = vault_root

    return settings


def _apply_config_dict(settings: AppSettings, data: dict[str, Any]) -> None:
    content = data.get("content")
    if content and isinstance(content, dict):
        for field in (
            "queue_poll_interval", "default_max_retries",
            "queue_ttl_seconds", "dlq_replay_interval",
            "enable_validation", "enable_rate_limiting",
            "enable_content_dedup", "planner_callbacks",
        ):
            if field in content:
                setattr(settings.content, field, content[field])

        respond_to = content.get("respond_to")
        if respond_to and isinstance(respond_to, list):
            settings.content.respond_to = set(respond_to)

        target_platforms = content.get("target_platforms")
        if target_platforms and isinstance(target_platforms, list):
            settings.content.target_platforms = target_platforms

        rate_limits = content.get("rate_limits")
        if rate_limits and isinstance(rate_limits, dict):
            settings.content.rate_limits.update(rate_limits)

        char_limits = content.get("platform_char_limits")
        if char_limits and isinstance(char_limits, dict):
            settings.content.platform_char_limits.update(char_limits)

    log_level = data.get("log_level")
    if log_level:
        settings.log_level = log_level.upper()

    max_workers = data.get("max_workers")
    if max_workers is not None:
        settings.max_workers = int(max_workers)

    credentials = data.get("credentials")
    if credentials and isinstance(credentials, dict):
        for platform_name, cred_data in credentials.items():
            try:
                platform = PlatformType(platform_name.lower())
                cred = Credentials(
                    access_token=interpolate_env_vars(cred_data.get("access_token", "")),
                    username=interpolate_env_vars(cred_data.get("username", "")),
                    password=interpolate_env_vars(cred_data.get("password", "")),
                )
                settings.credentials[platform] = cred
            except (ValueError, KeyError):
                _LOGGER.warning("Invalid platform in credentials | platform=%s", platform_name)

    watchers = data.get("watchers")
    if watchers and isinstance(watchers, dict):
        for platform_name, wc_data in watchers.items():
            try:
                platform = PlatformType(platform_name.lower())
                existing = settings.watchers.get(platform)
                if existing and isinstance(wc_data, dict):
                    for field, value in wc_data.items():
                        if hasattr(existing, field):
                            setattr(existing, field, value)
            except (ValueError, KeyError):
                _LOGGER.warning("Invalid platform in watchers | platform=%s", platform_name)


def _apply_env_overrides(settings: AppSettings, prefix: str) -> None:
    mapping = {
        f"{prefix}LOG_LEVEL": ("log_level", None),
        f"{prefix}MAX_WORKERS": ("max_workers", int),
        f"{prefix}QUEUE_POLL_INTERVAL": ("content.queue_poll_interval", float),
        f"{prefix}DEFAULT_MAX_RETRIES": ("content.default_max_retries", int),
        f"{prefix}QUEUE_TTL_SECONDS": ("content.queue_ttl_seconds", int),
        f"{prefix}DLQ_REPLAY_INTERVAL": ("content.dlq_replay_interval", int),
        f"{prefix}ENABLE_VALIDATION": ("content.enable_validation", _to_bool),
        f"{prefix}ENABLE_RATE_LIMITING": ("content.enable_rate_limiting", _to_bool),
        f"{prefix}ENABLE_CONTENT_DEDUP": ("content.enable_content_dedup", _to_bool),
        f"{prefix}PLANNER_CALLBACKS": ("content.planner_callbacks", _to_bool),
    }

    for env_key, (attr_path, converter) in mapping.items():
        value = os.environ.get(env_key)
        if value is not None:
            try:
                if converter:
                    value = converter(value)
                _set_nested_attr(settings, attr_path, value)
            except (ValueError, TypeError, AttributeError) as e:
                _LOGGER.warning(
                    "Failed to apply env override | env=%s | value=%s | error=%s",
                    env_key, value, e,
                )

    openai_key = get_secret("OPENAI_API_KEY")
    if openai_key:
        _LOGGER.debug("OPENAI_API_KEY found in secrets")


def _to_bool(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


def _set_nested_attr(obj: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)
