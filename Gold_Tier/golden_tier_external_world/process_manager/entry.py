from __future__ import annotations

from typing import Any, Optional
from pathlib import Path
from threading import Thread, Event
import argparse
import logging
import os
import signal
import sys
import time

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.posters.base import PostContent
from golden_tier_external_world.process_manager.ipc import (
    IpcMessage,
    read_ipc_line,
    write_ipc_line,
    MSG_TYPE_CMD_EVENT,
    MSG_TYPE_CMD_SHUTDOWN,
    MSG_TYPE_CMD_PING,
    MSG_TYPE_CMD_CONFIG,
)


POSTER_CLASSES: dict[str, type] = {}

try:
    from golden_tier_external_world.posters.facebook import FacebookPoster
    POSTER_CLASSES["facebook"] = FacebookPoster
except ImportError:
    pass

try:
    from golden_tier_external_world.posters.twitter import TwitterPoster
    POSTER_CLASSES["twitter"] = TwitterPoster
except ImportError:
    pass

try:
    from golden_tier_external_world.posters.instagram import InstagramPoster
    POSTER_CLASSES["instagram"] = InstagramPoster
except ImportError:
    pass


_HEARTBEAT_INTERVAL = 5.0


class WorkerProcess:
    def __init__(self, platform: str, vault_root: str, log_level: str = "INFO") -> None:
        self._platform = platform
        self._vault_root = Path(vault_root)
        self._running = False
        self._stop_event = Event()
        self._poster: Any = None
        self._logger = logging.getLogger(f"Worker[{platform}]")

        self._setup_logging(log_level)

    def _setup_logging(self, log_level: str) -> None:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root = logging.getLogger()
        root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        root.addHandler(handler)

    def _create_poster(self) -> Any:
        poster_cls = POSTER_CLASSES.get(self._platform)
        if poster_cls is None:
            raise RuntimeError(
                f"No poster implementation available for platform: {self._platform}"
            )

        vault_path = self._vault_root / self._platform
        vault_path.mkdir(parents=True, exist_ok=True)

        from golden_tier_external_world.storage.file_storage import FileStorage
        from golden_tier_external_world.events.bus import LocalEventBus

        storage = FileStorage(vault_path / "data")
        event_bus = LocalEventBus()
        event_bus.start()

        return poster_cls(
            storage=storage,
            event_bus=event_bus,
            headless=True,
            screenshot_dir=vault_path / "screenshots",
        )

    def _handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = payload.get("event_type", "")
        data = payload.get("data", {})

        self._logger.info("Processing event | type=%s", event_type)

        if event_type == "post":
            content = PostContent(
                text=data.get("text", ""),
                media_paths=data.get("media_paths", []),
            )
            if not self._poster._authenticated:
                self._logger.info("Authenticating before post")
                if not self._poster.authenticate():
                    return {"status": "error", "error": "Authentication failed"}

            post_id = self._poster.post(content)
            return {"status": "ok", "data": {"post_id": post_id}}

        if event_type == "authenticate":
            result = self._poster.authenticate()
            return {"status": "ok" if result else "error", "data": {"authenticated": result}}

        if event_type == "status":
            return {
                "status": "ok",
                "data": {
                    "authenticated": self._poster._authenticated,
                    "platform": self._platform,
                },
            }

        return {"status": "error", "error": f"Unknown event type: {event_type}"}

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                msg = IpcMessage.heartbeat(pid=os.getpid())
                write_ipc_line(sys.stdout, msg)
            except OSError:
                break
            self._stop_event.wait(_HEARTBEAT_INTERVAL)

    def run(self) -> None:
        self._running = True
        self._logger.info("Worker starting | platform=%s | pid=%d", self._platform, os.getpid())

        signal.signal(signal.SIGINT, lambda *_: None)
        signal.signal(signal.SIGTERM, lambda *_: None)

        try:
            self._poster = self._create_poster()
        except Exception as e:
            self._logger.error("Failed to create poster | error=%s", e)
            msg = IpcMessage.error(f"Poster creation failed: {e}")
            write_ipc_line(sys.stdout, msg)
            return

        heartbeat_thread = Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        ready_msg = IpcMessage.ready(platform=self._platform, pid=os.getpid())
        write_ipc_line(sys.stdout, ready_msg)

        self._logger.info("Worker ready | platform=%s", self._platform)

        while not self._stop_event.is_set():
            try:
                msg = read_ipc_line(sys.stdin, timeout=1.0)
            except Exception:
                continue

            if msg is None:
                continue

            if msg.type == MSG_TYPE_CMD_SHUTDOWN:
                self._logger.info("Shutdown command received")
                self._stop_event.set()
                write_ipc_line(sys.stdout, IpcMessage.shutdown_complete())
                break

            elif msg.type == MSG_TYPE_CMD_PING:
                write_ipc_line(sys.stdout, IpcMessage.heartbeat(pid=os.getpid()))

            elif msg.type == MSG_TYPE_CMD_EVENT:
                try:
                    result = self._handle_event(msg.payload)
                    write_ipc_line(
                        sys.stdout,
                        IpcMessage.result(
                            msg_id=msg.msg_id,
                            status=result.get("status", "ok"),
                            data=result.get("data"),
                            error=result.get("error"),
                        ),
                    )
                except Exception as e:
                    self._logger.error("Event handling failed | error=%s", e)
                    write_ipc_line(
                        sys.stdout,
                        IpcMessage.result(
                            msg_id=msg.msg_id,
                            status="error",
                            error=str(e),
                        ),
                    )

            elif msg.type == MSG_TYPE_CMD_CONFIG:
                self._logger.info("Config update received | payload=%s", msg.payload)

        self._logger.info("Worker stopped | platform=%s", self._platform)
        self._running = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Social media worker subprocess")
    parser.add_argument("--platform", required=True, choices=list(POSTER_CLASSES.keys()))
    parser.add_argument("--vault-root", required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    worker = WorkerProcess(
        platform=args.platform,
        vault_root=args.vault_root,
        log_level=args.log_level,
    )
    worker.run()


if __name__ == "__main__":
    main()
