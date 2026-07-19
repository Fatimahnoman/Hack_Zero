from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import logging
import os
import re


_SECRET_KEYS_CACHE: dict[str, str] = {}
_LOGGER = logging.getLogger("secrets")


def _load_dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result

    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                result[key] = value
    except Exception as e:
        _LOGGER.warning("Failed to load .env file | path=%s | error=%s", path, e)

    return result


def load_secrets(
    env_file: Optional[Path] = None,
    override_environ: bool = False,
) -> dict[str, str]:
    secrets: dict[str, str] = {}

    if env_file and env_file.exists():
        dotenv = _load_dotenv(env_file)
        secrets.update(dotenv)
        if override_environ:
            for k, v in dotenv.items():
                os.environ.setdefault(k, v)

    for key in (
        "OPENAI_API_KEY",
        "TWITTER_BEARER_TOKEN",
        "TWITTER_API_KEY",
        "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_TOKEN_SECRET",
        "FACEBOOK_ACCESS_TOKEN",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_EMAIL",
        "INSTAGRAM_PASSWORD",
        "LINKEDIN_ACCESS_TOKEN",
        "LINKEDIN_EMAIL",
        "LINKEDIN_PASSWORD",
        "CAPTCHA_API_KEY",
    ):
        value = os.environ.get(key)
        if value:
            secrets[key] = value

    _SECRET_KEYS_CACHE.update(secrets)
    return secrets


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    if key in _SECRET_KEYS_CACHE:
        return _SECRET_KEYS_CACHE[key]

    value = os.environ.get(key)
    if value is not None:
        _SECRET_KEYS_CACHE[key] = value
        return value

    return default


def redact(value: Optional[str]) -> str:
    if value is None:
        return "None"
    if len(value) <= 8:
        return value[:2] + "***"
    return value[:4] + "****" + value[-4:]


def resolve_secrets_in_config(config_dict: dict[str, Any]) -> dict[str, Any]:
    result = dict(config_dict)
    for key, value in result.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_key = value[2:-1]
            env_value = get_secret(env_key)
            if env_value is not None:
                result[key] = env_value
    return result


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def interpolate_env_vars(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        env_key = match.group(1)
        env_value = get_secret(env_key)
        if env_value is not None:
            return env_value
        _LOGGER.warning("Environment variable not found | key=%s", env_key)
        return match.group(0)

    return _ENV_VAR_PATTERN.sub(_replace, value)
