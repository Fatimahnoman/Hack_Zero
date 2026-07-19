from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


@dataclass
class PostResult:
    platform: str
    post_id: Optional[str] = None
    source_event_id: str = ""
    queue_item_id: str = ""
    status: str = "pending"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    duration_ms: Optional[float] = None
    screenshot_path: Optional[str] = None
    trace_path: Optional[str] = None
    retry_count: int = 0
    error: Optional[str] = None
    result_id: str = field(default_factory=lambda: uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostResult:
        return cls(**data)
