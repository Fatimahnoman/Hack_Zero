from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4
import json


MSG_TYPE_HEARTBEAT = "heartbeat"
MSG_TYPE_READY = "ready"
MSG_TYPE_RESULT = "result"
MSG_TYPE_LOG = "log"
MSG_TYPE_SHUTDOWN_COMPLETE = "shutdown_complete"
MSG_TYPE_ERROR = "error"

MSG_TYPE_CMD_EVENT = "cmd_event"
MSG_TYPE_CMD_SHUTDOWN = "cmd_shutdown"
MSG_TYPE_CMD_PING = "cmd_ping"
MSG_TYPE_CMD_CONFIG = "cmd_config"


@dataclass
class IpcMessage:
    type: str
    msg_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, line: str) -> IpcMessage:
        data = json.loads(line)
        return cls(**data)

    @classmethod
    def heartbeat(cls, pid: int, status: str = "running") -> IpcMessage:
        return cls(
            type=MSG_TYPE_HEARTBEAT,
            payload={"pid": pid, "status": status},
        )

    @classmethod
    def ready(cls, platform: str, pid: int) -> IpcMessage:
        return cls(
            type=MSG_TYPE_READY,
            payload={"platform": platform, "pid": pid},
        )

    @classmethod
    def result(cls, msg_id: str, status: str, data: Optional[dict[str, Any]] = None, error: Optional[str] = None) -> IpcMessage:
        payload: dict[str, Any] = {"status": status}
        if data is not None:
            payload["data"] = data
        if error is not None:
            payload["error"] = error
        return cls(type=MSG_TYPE_RESULT, payload=payload, msg_id=msg_id)

    @classmethod
    def log(cls, level: str, message: str, extra: Optional[dict[str, Any]] = None) -> IpcMessage:
        payload: dict[str, Any] = {"level": level, "message": message}
        if extra:
            payload["extra"] = extra
        return cls(type=MSG_TYPE_LOG, payload=payload)

    @classmethod
    def shutdown_complete(cls) -> IpcMessage:
        return cls(type=MSG_TYPE_SHUTDOWN_COMPLETE, payload={})

    @classmethod
    def error(cls, error: str, msg_id: Optional[str] = None) -> IpcMessage:
        payload: dict[str, Any] = {"error": error}
        return cls(type=MSG_TYPE_ERROR, payload=payload, msg_id=msg_id or uuid4().hex)

    @classmethod
    def cmd_event(cls, event_data: dict[str, Any]) -> IpcMessage:
        return cls(type=MSG_TYPE_CMD_EVENT, payload=event_data)

    @classmethod
    def cmd_shutdown(cls, graceful: bool = True, timeout_seconds: float = 30.0) -> IpcMessage:
        return cls(
            type=MSG_TYPE_CMD_SHUTDOWN,
            payload={"graceful": graceful, "timeout_seconds": timeout_seconds},
        )

    @classmethod
    def cmd_ping(cls) -> IpcMessage:
        return cls(type=MSG_TYPE_CMD_PING, payload={})

    @classmethod
    def cmd_config(cls, config: dict[str, Any]) -> IpcMessage:
        return cls(type=MSG_TYPE_CMD_CONFIG, payload=config)


def read_ipc_line(stream, timeout: float = None) -> Optional[IpcMessage]:
    try:
        import selectors
        try:
            fileno = stream.fileno()
        except (OSError, ValueError):
            fileno = -1
        if fileno != -1:
            sel = selectors.DefaultSelector()
            try:
                sel.register(stream, selectors.EVENT_READ)
                events = sel.select(timeout=timeout or 0.1)
                if not events:
                    return None
            finally:
                sel.close()

        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return None
        return IpcMessage.from_json(line)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def write_ipc_line(stream, message: IpcMessage) -> None:
    stream.write(message.to_json() + "\n")
    stream.flush()
