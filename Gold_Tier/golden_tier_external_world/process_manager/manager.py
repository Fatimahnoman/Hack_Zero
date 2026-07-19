from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread, Event, RLock
from typing import Any, Optional
import logging
import os
import signal
import subprocess
import sys
import time

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.process_manager.ipc import (
    IpcMessage,
    read_ipc_line,
    write_ipc_line,
    MSG_TYPE_HEARTBEAT,
    MSG_TYPE_READY,
    MSG_TYPE_RESULT,
    MSG_TYPE_LOG,
    MSG_TYPE_SHUTDOWN_COMPLETE,
    MSG_TYPE_ERROR,
    MSG_TYPE_CMD_EVENT,
    MSG_TYPE_CMD_SHUTDOWN,
)


_HEARTBEAT_TIMEOUT = 15.0
_MONITOR_INTERVAL = 2.0
_MAX_RESTARTS_PER_WINDOW = 5
_RESTART_WINDOW_SECONDS = 300.0


@dataclass
class WorkerProcessInfo:
    platform: PlatformType
    process: Optional[subprocess.Popen[bytes]]
    pid: Optional[int]
    status: str  # starting, running, stopping, stopped, crashed
    last_heartbeat: float
    restart_count: int
    first_restart_time: float
    ready: bool
    pending_results: dict[str, dict[str, Any]]


class ProcessError(Exception):
    pass


class ProcessManager:
    def __init__(
        self,
        vault_root: Path,
        platforms: Optional[list[PlatformType]] = None,
        heartbeat_timeout: float = _HEARTBEAT_TIMEOUT,
        monitor_interval: float = _MONITOR_INTERVAL,
        max_restarts_per_window: int = _MAX_RESTARTS_PER_WINDOW,
        restart_window_seconds: float = _RESTART_WINDOW_SECONDS,
        log_level: str = "INFO",
    ) -> None:
        self._vault_root = vault_root
        self._platforms = platforms or [
            PlatformType.FACEBOOK,
            PlatformType.TWITTER,
            PlatformType.INSTAGRAM,
        ]
        self._heartbeat_timeout = heartbeat_timeout
        self._monitor_interval = monitor_interval
        self._max_restarts_per_window = max_restarts_per_window
        self._restart_window_seconds = restart_window_seconds
        self._log_level = log_level

        self._workers: dict[PlatformType, WorkerProcessInfo] = {}
        self._lock = RLock()
        self._running = False
        self._stop_event = Event()
        self._monitor_thread: Optional[Thread] = None
        self._logger = logging.getLogger("ProcessManager")

        vault_root.mkdir(parents=True, exist_ok=True)
        for p in self._platforms:
            (vault_root / p.value).mkdir(parents=True, exist_ok=True)

        self._logger.info(
            "ProcessManager initialized | platforms=%s | heartbeat_timeout=%.1fs | max_restarts=%d",
            [p.value for p in self._platforms],
            self._heartbeat_timeout,
            self._max_restarts_per_window,
        )

    # ── Public API ─────────────────────────────────────────────

    def start_all(self) -> None:
        if self._running:
            self._logger.warning("ProcessManager already running")
            return

        self._running = True
        self._stop_event.clear()

        for platform in self._platforms:
            self._start_worker(platform)

        self._monitor_thread = Thread(
            target=self._monitor_loop,
            name="procman-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

        self._logger.info("ProcessManager started | workers=%d", len(self._platforms))

    def stop_all(self, timeout: float = 30.0) -> None:
        self._logger.info("Stopping all workers | timeout=%.1fs", timeout)
        self._running = False
        self._stop_event.set()

        with self._lock:
            platforms = list(self._workers.keys())

        for platform in platforms:
            self._stop_worker(platform, graceful=True)

        deadline = time.monotonic() + timeout
        for platform in platforms:
            remaining = max(0.0, deadline - time.monotonic())
            self._wait_worker(platform, timeout=remaining)
            self._force_kill(platform)

        self._logger.info("ProcessManager stopped")

    def restart(self, platform: PlatformType) -> None:
        self._logger.info("Restarting worker | platform=%s", platform.value)
        self._stop_worker(platform, graceful=False)
        self._start_worker(platform)

    def restart_all(self) -> None:
        for platform in self._platforms:
            self.restart(platform)

    def send_event(
        self,
        platform: PlatformType,
        event_data: dict[str, Any],
        timeout: float = 30.0,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            info = self._workers.get(platform)
            if info is None or info.process is None or info.process.stdin is None:
                self._logger.warning("Worker not available | platform=%s", platform.value)
                return None

            msg = IpcMessage.cmd_event(event_data)
            try:
                write_ipc_line(info.process.stdin, msg)
            except OSError as e:
                self._logger.error("IPC write failed | platform=%s | error=%s", platform.value, e)
                self._handle_crash(platform)
                return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if msg.msg_id in info.pending_results:
                    result = info.pending_results.pop(msg.msg_id)
                    return result
            time.sleep(0.1)

        self._logger.warning("Event result timeout | platform=%s | msg_id=%s", platform.value, msg.msg_id)
        return None

    def worker_status(self, platform: PlatformType) -> Optional[dict[str, Any]]:
        with self._lock:
            info = self._workers.get(platform)
            if info is None:
                return None
            return {
                "platform": info.platform.value,
                "pid": info.pid,
                "status": info.status,
                "alive": info.process is not None and info.process.poll() is None,
                "ready": info.ready,
                "last_heartbeat": info.last_heartbeat,
                "restart_count": info.restart_count,
                "uptime": self._worker_uptime(info),
            }

    def all_status(self) -> dict[str, dict[str, Any]]:
        return {
            p.value: s for p, s in ((p, self.worker_status(p)) for p in self._platforms)
            if s is not None
        }

    # ── Worker Lifecycle ───────────────────────────────────────

    def _start_worker(self, platform: PlatformType) -> None:
        with self._lock:
            if self._exceeds_restart_limit(platform):
                self._logger.error(
                    "Restart limit exceeded | platform=%s | restarts=%d",
                    platform.value,
                    self._workers[platform].restart_count,
                )
                return

            info = WorkerProcessInfo(
                platform=platform,
                process=None,
                pid=None,
                status="starting",
                last_heartbeat=time.time(),
                restart_count=self._workers.get(platform, WorkerProcessInfo(
                    platform=platform, process=None, pid=None, status="stopped",
                    last_heartbeat=0.0, restart_count=0, first_restart_time=0.0,
                    ready=False, pending_results={},
                )).restart_count + 1 if platform in self._workers else 0,
                first_restart_time=time.time() if platform not in self._workers or not self._workers[platform].first_restart_time else self._workers[platform].first_restart_time,
                ready=False,
                pending_results={},
            )

            if info.restart_count == 1:
                info.first_restart_time = time.time()

            self._workers[platform] = info

        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m", "golden_tier_external_world.process_manager.entry",
                    "--platform", platform.value,
                    "--vault-root", str(self._vault_root),
                    "--log-level", self._log_level,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            with self._lock:
                self._workers[platform].process = proc
                self._workers[platform].pid = proc.pid

            self._logger.info(
                "Worker started | platform=%s | pid=%d | restarts=%d",
                platform.value,
                proc.pid,
                info.restart_count,
            )

        except Exception as e:
            self._logger.error(
                "Failed to start worker | platform=%s | error=%s",
                platform.value,
                e,
            )
            with self._lock:
                self._workers[platform].status = "crashed"

    def _stop_worker(self, platform: PlatformType, graceful: bool = True) -> None:
        with self._lock:
            info = self._workers.get(platform)
            if info is None or info.process is None:
                return
            info.status = "stopping"

        if graceful and info.process.stdin:
            try:
                msg = IpcMessage.cmd_shutdown(graceful=True, timeout_seconds=10.0)
                write_ipc_line(info.process.stdin, msg)
                self._logger.info("Shutdown signal sent | platform=%s | pid=%d", platform.value, info.pid)
                return
            except OSError:
                pass

        self._force_kill(platform)

    def _wait_worker(self, platform: PlatformType, timeout: float = 10.0) -> bool:
        with self._lock:
            info = self._workers.get(platform)
            if info is None or info.process is None:
                return True

        try:
            info.process.wait(timeout=timeout)
            with self._lock:
                self._workers[platform].status = "stopped"
            self._logger.info("Worker exited | platform=%s | pid=%d", platform.value, info.pid)
            return True
        except subprocess.TimeoutExpired:
            self._logger.warning("Worker did not exit in time | platform=%s | timeout=%.1fs", platform.value, timeout)
            return False

    def _force_kill(self, platform: PlatformType) -> None:
        with self._lock:
            info = self._workers.get(platform)
            if info is None or info.process is None:
                return
            pid = info.pid

        try:
            info.process.kill()
            info.process.wait(timeout=5.0)
            self._logger.info("Worker killed | platform=%s | pid=%d", platform.value, pid)
        except Exception as e:
            self._logger.error("Force kill failed | platform=%s | pid=%d | error=%s", platform.value, pid, e)

        with self._lock:
            self._workers[platform].status = "stopped"
            self._workers[platform].process = None
            self._workers[platform].pid = None

    def _handle_crash(self, platform: PlatformType) -> None:
        self._logger.warning("Worker crashed | platform=%s", platform.value)
        with self._lock:
            info = self._workers.get(platform)
            if info:
                info.status = "crashed"
                info.ready = False

        if self._running:
            self._logger.info("Auto-restarting worker | platform=%s", platform.value)
            self._start_worker(platform)

    # ── Monitoring ─────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            self._check_heartbeats()
            self._read_worker_output()
            self._check_exited_processes()
            self._stop_event.wait(self._monitor_interval)

    def _check_heartbeats(self) -> None:
        now = time.time()
        with self._lock:
            for platform, info in list(self._workers.items()):
                if info.status in ("stopped", "stopping"):
                    continue
                if info.ready and (now - info.last_heartbeat) > self._heartbeat_timeout:
                    self._logger.error(
                        "Heartbeat timeout | platform=%s | pid=%d | last=%.1fs ago",
                        platform.value,
                        info.pid,
                        now - info.last_heartbeat,
                    )
                    self._force_kill(platform)
                    if self._running:
                        self._start_worker(platform)

    def _read_worker_output(self) -> None:
        with self._lock:
            workers = list(self._workers.items())

        for platform, info in workers:
            if info.process is None or info.process.stdout is None:
                continue

            try:
                while True:
                    msg = read_ipc_line(info.process.stdout, timeout=0.01)
                    if msg is None:
                        break
                    self._handle_ipc_message(platform, msg)
            except Exception:
                break

    def _check_exited_processes(self) -> None:
        with self._lock:
            for platform, info in list(self._workers.items()):
                if info.process is None:
                    continue
                ret = info.process.poll()
                if ret is not None:
                    self._logger.warning(
                        "Process exited unexpectedly | platform=%s | pid=%d | returncode=%d",
                        platform.value,
                        info.pid,
                        ret,
                    )
                    self._handle_crash(platform)

    def _handle_ipc_message(self, platform: PlatformType, msg: IpcMessage) -> None:
        with self._lock:
            info = self._workers.get(platform)
            if info is None:
                return

            if msg.type == MSG_TYPE_HEARTBEAT:
                info.last_heartbeat = time.time()

            elif msg.type == MSG_TYPE_READY:
                info.ready = True
                info.status = "running"
                self._logger.info(
                    "Worker ready | platform=%s | pid=%d",
                    platform.value,
                    msg.payload.get("pid"),
                )

            elif msg.type == MSG_TYPE_RESULT:
                msg_id = msg.msg_id
                info.pending_results[msg_id] = msg.payload

            elif msg.type == MSG_TYPE_LOG:
                level = msg.payload.get("level", "info").upper()
                message = msg.payload.get("message", "")
                extra = msg.payload.get("extra")
                log_msg = f"[{platform.value}] {message}"
                if extra:
                    log_msg += f" | extra={extra}"
                if level == "ERROR":
                    self._logger.error(log_msg)
                elif level == "WARNING":
                    self._logger.warning(log_msg)
                else:
                    self._logger.info(log_msg)

            elif msg.type == MSG_TYPE_SHUTDOWN_COMPLETE:
                self._logger.info("Worker shutdown complete | platform=%s", platform.value)

            elif msg.type == MSG_TYPE_ERROR:
                self._logger.error(
                    "Worker error | platform=%s | error=%s",
                    platform.value,
                    msg.payload.get("error"),
                )

    # ── Helpers ────────────────────────────────────────────────

    def _exceeds_restart_limit(self, platform: PlatformType) -> bool:
        info = self._workers.get(platform)
        if info is None:
            return False
        if info.restart_count >= self._max_restarts_per_window:
            elapsed = time.time() - info.first_restart_time
            if elapsed < self._restart_window_seconds:
                return True
        return False

    @staticmethod
    def _worker_uptime(info: WorkerProcessInfo) -> Optional[float]:
        if info.process is None:
            return None
        try:
            create_time = info.process.pid
            import psutil
            return time.time() - psutil.Process(create_time).create_time()
        except Exception:
            return None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_workers(self) -> int:
        with self._lock:
            return sum(
                1 for info in self._workers.values()
                if info.status == "running" and info.ready
            )
