from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from golden_tier_external_world.config.enums import PlatformType


@dataclass
class WatcherConfig:
    platform: PlatformType
    poll_interval_seconds: int = 60
    max_events_per_poll: int = 50
    enabled: bool = True
    headless: bool = True
    timeout_seconds: int = 30
    max_retries: int = 3
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    backoff_jitter: float = 0.5
    heartbeat_interval_polls: int = 10
    sleep_granularity: float = 0.5

    def __post_init__(self) -> None:
        if self.poll_interval_seconds < 1:
            raise ValueError("poll_interval_seconds must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")


@dataclass
class Credentials:
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return any([
            self.access_token is not None,
            self.username is not None,
            self.client_id is not None,
        ])


@dataclass
class StorageConfig:
    vault_path: Path
    processed_ids_file: str = "processed_ids.json"
    events_dir: str = "events"
    max_archive_days: int = 90


@dataclass
class ContentConfig:
    respond_to: set[str] = field(default_factory=lambda: {"MESSAGE", "COMMENT", "MENTION"})
    target_platforms: list[str] = field(
        default_factory=lambda: ["facebook", "twitter", "instagram"]
    )
    queue_poll_interval: float = 2.0
    default_max_retries: int = 3
    queue_ttl_seconds: Optional[int] = None
    dlq_replay_interval: int = 10
    enable_validation: bool = True
    enable_rate_limiting: bool = True
    enable_content_dedup: bool = True
    planner_callbacks: bool = True

    rate_limits: dict[str, list[int]] = field(default_factory=lambda: {
        "facebook": [200, 86400],
        "twitter": [300, 86400],
        "instagram": [100, 86400],
        "linkedin": [100, 86400],
    })

    platform_char_limits: dict[str, int] = field(default_factory=lambda: {
        "twitter": 280,
        "facebook": 63206,
        "instagram": 2200,
        "linkedin": 3000,
    })


@dataclass
class AppSettings:
    vault_path: Path
    storage: StorageConfig
    watchers: dict[PlatformType, WatcherConfig] = field(default_factory=dict)
    credentials: dict[PlatformType, Credentials] = field(default_factory=dict)
    content: ContentConfig = field(default_factory=ContentConfig)
    log_level: str = "INFO"
    max_workers: int = 4

    @classmethod
    def defaults(cls, vault_path: Path) -> "AppSettings":
        return cls(
            vault_path=vault_path,
            storage=StorageConfig(vault_path=vault_path),
            watchers={
                PlatformType.FACEBOOK: WatcherConfig(platform=PlatformType.FACEBOOK),
                PlatformType.TWITTER: WatcherConfig(platform=PlatformType.TWITTER),
                PlatformType.INSTAGRAM: WatcherConfig(platform=PlatformType.INSTAGRAM),
                PlatformType.LINKEDIN: WatcherConfig(platform=PlatformType.LINKEDIN),
            },
        )
