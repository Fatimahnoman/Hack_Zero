from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from golden_tier_external_world.config.enums import PlatformType


@dataclass(frozen=True)
class PlatformAccount:
    platform: PlatformType
    account_id: str
    display_name: str
    username: str
    platform_id: str = ""
    profile_url: Optional[str] = None
    avatar_url: Optional[str] = None
    verified: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.platform.value}/{self.username}"
