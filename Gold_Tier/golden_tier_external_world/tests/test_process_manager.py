from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest import TestCase, mock
import json
import os
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
    MSG_TYPE_CMD_PING,
    MSG_TYPE_CMD_CONFIG,
)
from golden_tier_external_world.process_manager.manager import (
    ProcessManager,
    ProcessError,
    _HEARTBEAT_TIMEOUT,
    _MONITOR_INTERVAL,
    _MAX_RESTARTS_PER_WINDOW,
)


def _make_pm(
    vault_root: Path = Path("/tmp/test_vault"),
    heartbeat_timeout: float = _HEARTBEAT_TIMEOUT,
    monitor_interval: float = _MONITOR_INTERVAL,
    max_restarts_per_window: int = _MAX_RESTARTS_PER_WINDOW,
    restart_window_seconds: float = 300.0,
    log_level: str = "INFO",
) -> ProcessManager:
    return ProcessManager(
        vault_root=vault_root,
        heartbeat_timeout=heartbeat_timeout,
        monitor_interval=monitor_interval,
        max_restarts_per_window=max_restarts_per_window,
        restart_window_seconds=restart_window_seconds,
        log_level=log_level,
    )


# ── IPC Message Tests ─────────────────────────────────────────


class TestIpcMessage(TestCase):
    def test_heartbeat_message(self) -> None:
        msg = IpcMessage.heartbeat(pid=1234)
        self.assertEqual(msg.type, MSG_TYPE_HEARTBEAT)
        self.assertEqual(msg.payload["pid"], 1234)
        self.assertEqual(msg.payload["status"], "running")

    def test_ready_message(self) -> None:
        msg = IpcMessage.ready(platform="facebook", pid=5678)
        self.assertEqual(msg.type, MSG_TYPE_READY)
        self.assertEqual(msg.payload["platform"], "facebook")
        self.assertEqual(msg.payload["pid"], 5678)

    def test_result_message(self) -> None:
        msg = IpcMessage.result(msg_id="abc", status="ok", data={"post_id": "123"})
        self.assertEqual(msg.type, MSG_TYPE_RESULT)
        self.assertEqual(msg.msg_id, "abc")
        self.assertEqual(msg.payload["status"], "ok")
        self.assertEqual(msg.payload["data"]["post_id"], "123")

    def test_result_error(self) -> None:
        msg = IpcMessage.result(msg_id="abc", status="error", error="Something failed")
        self.assertEqual(msg.payload["status"], "error")
        self.assertEqual(msg.payload["error"], "Something failed")

    def test_log_message(self) -> None:
        msg = IpcMessage.log(level="info", message="test log", extra={"key": "val"})
        self.assertEqual(msg.type, MSG_TYPE_LOG)
        self.assertEqual(msg.payload["level"], "info")
        self.assertEqual(msg.payload["message"], "test log")
        self.assertEqual(msg.payload["extra"]["key"], "val")

    def test_shutdown_complete(self) -> None:
        msg = IpcMessage.shutdown_complete()
        self.assertEqual(msg.type, MSG_TYPE_SHUTDOWN_COMPLETE)

    def test_error_message(self) -> None:
        msg = IpcMessage.error("Something went wrong")
        self.assertEqual(msg.type, MSG_TYPE_ERROR)
        self.assertEqual(msg.payload["error"], "Something went wrong")

    def test_cmd_event(self) -> None:
        msg = IpcMessage.cmd_event({"event_type": "post", "data": {"text": "hello"}})
        self.assertEqual(msg.type, MSG_TYPE_CMD_EVENT)
        self.assertEqual(msg.payload["event_type"], "post")
        self.assertEqual(msg.payload["data"]["text"], "hello")

    def test_cmd_shutdown(self) -> None:
        msg = IpcMessage.cmd_shutdown(graceful=True, timeout_seconds=15.0)
        self.assertEqual(msg.type, MSG_TYPE_CMD_SHUTDOWN)
        self.assertEqual(msg.payload["graceful"], True)
        self.assertEqual(msg.payload["timeout_seconds"], 15.0)

    def test_cmd_ping(self) -> None:
        msg = IpcMessage.cmd_ping()
        self.assertEqual(msg.type, MSG_TYPE_CMD_PING)

    def test_cmd_config(self) -> None:
        msg = IpcMessage.cmd_config({"headless": False})
        self.assertEqual(msg.type, MSG_TYPE_CMD_CONFIG)
        self.assertEqual(msg.payload["headless"], False)

    def test_json_roundtrip(self) -> None:
        original = IpcMessage.heartbeat(pid=42)
        json_str = original.to_json()
        decoded = IpcMessage.from_json(json_str)
        self.assertEqual(original.type, decoded.type)
        self.assertEqual(original.msg_id, decoded.msg_id)
        self.assertEqual(original.payload["pid"], decoded.payload["pid"])

    def test_from_json(self) -> None:
        raw = '{"type": "heartbeat", "msg_id": "x1", "timestamp": 0.0, "payload": {"pid": 99}}'
        msg = IpcMessage.from_json(raw)
        self.assertEqual(msg.type, "heartbeat")
        self.assertEqual(msg.payload["pid"], 99)

    def test_default_fields(self) -> None:
        msg = IpcMessage(type="test")
        self.assertTrue(len(msg.msg_id) > 0)
        self.assertGreater(msg.timestamp, 0)
        self.assertEqual(msg.payload, {})


class TestIpcReadWrite(TestCase):
    def test_write_read_roundtrip(self) -> None:
        import io
        stream = io.StringIO()
        original = IpcMessage.heartbeat(pid=7)
        write_ipc_line(stream, original)
        stream.seek(0)
        decoded = read_ipc_line(stream)
        assert decoded is not None
        self.assertEqual(original.type, decoded.type)
        self.assertEqual(original.payload["pid"], decoded.payload["pid"])

    def test_read_empty_stream(self) -> None:
        import io
        stream = io.StringIO()
        result = read_ipc_line(stream, timeout=0.1)
        self.assertIsNone(result)

    def test_read_invalid_json(self) -> None:
        import io
        stream = io.StringIO("not json\n")
        result = read_ipc_line(stream, timeout=0.1)
        self.assertIsNone(result)


# ── ProcessManager Tests ──────────────────────────────────────


class TestProcessManager(TestCase):
    def setUp(self) -> None:
        self.vault = Path("/tmp/test_pm_vault")
        self.vault.mkdir(parents=True, exist_ok=True)
        self.pm = _make_pm(vault_root=self.vault)

    def tearDown(self) -> None:
        import shutil
        if self.vault.exists():
            shutil.rmtree(self.vault, ignore_errors=True)

    def test_initial_state(self) -> None:
        self.assertFalse(self.pm.is_running)
        self.assertEqual(self.pm.active_workers, 0)

    def test_worker_status_none_for_unstarted(self) -> None:
        status = self.pm.worker_status(PlatformType.FACEBOOK)
        self.assertIsNone(status)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_start_worker_creates_subprocess(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._start_worker(PlatformType.FACEBOOK)

        status = self.pm.worker_status(PlatformType.FACEBOOK)
        assert status is not None
        self.assertEqual(status["platform"], "facebook")
        self.assertEqual(status["pid"], 12345)
        self.assertEqual(status["status"], "starting")

        mock_popen.assert_called_once()

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_start_all_starts_all_platforms(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 1
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm.start_all()
        self.assertTrue(self.pm.is_running)
        self.assertEqual(mock_popen.call_count, 3)

        self.pm.stop_all(timeout=1)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_stop_worker_sends_shutdown(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 42
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._start_worker(PlatformType.TWITTER)
        self.pm._stop_worker(PlatformType.TWITTER, graceful=True)

        written = mock_proc.stdin.write.call_args[0][0]
        self.assertIn("cmd_shutdown", written)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_handle_ipc_heartbeat_updates_timestamp(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 7
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._start_worker(PlatformType.INSTAGRAM)
        old_ts = self.pm._workers[PlatformType.INSTAGRAM].last_heartbeat

        time.sleep(0.01)
        hb = IpcMessage.heartbeat(pid=7)
        self.pm._handle_ipc_message(PlatformType.INSTAGRAM, hb)

        new_ts = self.pm._workers[PlatformType.INSTAGRAM].last_heartbeat
        self.assertGreater(new_ts, old_ts)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_handle_ipc_ready_sets_running(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 8
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._start_worker(PlatformType.FACEBOOK)
        ready = IpcMessage.ready(platform="facebook", pid=8)
        self.pm._handle_ipc_message(PlatformType.FACEBOOK, ready)

        status = self.pm.worker_status(PlatformType.FACEBOOK)
        assert status is not None
        self.assertTrue(status["ready"])
        self.assertEqual(status["status"], "running")

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_send_event_writes_to_stdin(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 9
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._start_worker(PlatformType.FACEBOOK)
        ready = IpcMessage.ready(platform="facebook", pid=9)
        self.pm._handle_ipc_message(PlatformType.FACEBOOK, ready)

        msg_id = self.pm.send_event(
            PlatformType.FACEBOOK,
            {"event_type": "post", "data": {"text": "hello"}},
            timeout=0.5,
        )

        self.assertIsNone(msg_id)
        mock_proc.stdin.write.assert_called_once()

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_handle_crash_triggers_restart(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 10
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._running = True
        self.pm._start_worker(PlatformType.TWITTER)

        initial_count = self.pm._workers[PlatformType.TWITTER].restart_count
        self.pm._handle_crash(PlatformType.TWITTER)

        self.assertEqual(mock_popen.call_count, 2)  # initial + restart

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_heartbeat_timeout_triggers_restart(self, mock_popen: mock.MagicMock) -> None:
        proc1 = mock.MagicMock()
        proc1.pid = 11
        proc1.poll.return_value = None
        proc1.stdin = mock.MagicMock()
        proc1.stdout = mock.MagicMock()
        proc1.stdout.readline.return_value = ""

        proc2 = mock.MagicMock()
        proc2.pid = 12
        proc2.poll.return_value = None
        proc2.stdin = mock.MagicMock()
        proc2.stdout = mock.MagicMock()
        proc2.stdout.readline.return_value = ""

        mock_popen.side_effect = [proc1, proc2]

        self.pm._running = True
        self.pm._heartbeat_timeout = 0.1
        self.pm._start_worker(PlatformType.INSTAGRAM)

        ready = IpcMessage.ready(platform="instagram", pid=11)
        self.pm._handle_ipc_message(PlatformType.INSTAGRAM, ready)

        self.pm._workers[PlatformType.INSTAGRAM].last_heartbeat = 0
        self.pm._check_heartbeats()

        self.assertGreaterEqual(mock_popen.call_count, 2)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_exited_process_triggers_crash_handling(self, mock_popen: mock.MagicMock) -> None:
        proc = mock.MagicMock()
        proc.pid = 12
        proc.poll.return_value = 1
        proc.stdin = mock.MagicMock()
        proc.stdout = mock.MagicMock()
        proc.stdout.readline.return_value = ""

        restarted_proc = mock.MagicMock()
        restarted_proc.pid = 13
        restarted_proc.poll.return_value = None
        restarted_proc.stdin = mock.MagicMock()
        restarted_proc.stdout = mock.MagicMock()
        restarted_proc.stdout.readline.return_value = ""

        mock_popen.side_effect = [proc, restarted_proc]

        self.pm._running = True
        self.pm._start_worker(PlatformType.FACEBOOK)
        self.pm._check_exited_processes()

        self.assertEqual(mock_popen.call_count, 2)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_all_status_returns_all_platforms(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 13
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._start_worker(PlatformType.FACEBOOK)
        self.pm._start_worker(PlatformType.TWITTER)
        self.pm._start_worker(PlatformType.INSTAGRAM)

        statuses = self.pm.all_status()
        self.assertIn("facebook", statuses)
        self.assertIn("twitter", statuses)
        self.assertIn("instagram", statuses)

    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_restart_limit_exceeded(self, mock_popen: mock.MagicMock) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.pid = 14
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        self.pm._max_restarts_per_window = 2
        self.pm._restart_window_seconds = 99999
        self.pm._running = True

        self.pm._start_worker(PlatformType.FACEBOOK)
        self.pm._handle_crash(PlatformType.FACEBOOK)
        self.pm._handle_crash(PlatformType.FACEBOOK)
        self.pm._handle_crash(PlatformType.FACEBOOK)

        self.assertEqual(mock_popen.call_count, 3)  # initial + 2 restarts (3rd blocked)

    def test_worker_status_none(self) -> None:
        status = self.pm.worker_status(PlatformType.FACEBOOK)
        self.assertIsNone(status)

    def test_send_event_no_worker(self) -> None:
        result = self.pm.send_event(PlatformType.FACEBOOK, {"test": "data"}, timeout=0.5)
        self.assertIsNone(result)


class TestProcessManagerStructuredLogging(TestCase):
    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_worker_log_messages_forwarded(self, mock_popen: mock.MagicMock) -> None:
        vault = Path("/tmp/test_pm_log_vault")
        vault.mkdir(parents=True, exist_ok=True)
        pm = _make_pm(vault_root=vault)

        mock_proc = mock.MagicMock()
        mock_proc.pid = 20
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        pm._start_worker(PlatformType.FACEBOOK)

        log_msg = IpcMessage.log(level="info", message="test worker log", extra={"cpu": 0.5})
        pm._handle_ipc_message(PlatformType.FACEBOOK, log_msg)

        error_msg = IpcMessage.error("Something went wrong", msg_id="e1")
        pm._handle_ipc_message(PlatformType.FACEBOOK, error_msg)

        import shutil
        shutil.rmtree(vault, ignore_errors=True)


class TestProcessManagerStartStopAll(TestCase):
    @mock.patch("golden_tier_external_world.process_manager.manager.subprocess.Popen")
    def test_start_stop_all_lifecycle(self, mock_popen: mock.MagicMock) -> None:
        vault = Path("/tmp/test_pm_lifecycle_vault")
        vault.mkdir(parents=True, exist_ok=True)
        pm = _make_pm(vault_root=vault)

        mock_proc = mock.MagicMock()
        mock_proc.pid = 30
        mock_proc.poll.return_value = None
        mock_proc.stdin = mock.MagicMock()
        mock_proc.stdout = mock.MagicMock()
        mock_proc.stdout.readline.return_value = ""
        mock_popen.return_value = mock_proc

        pm.start_all()
        self.assertTrue(pm.is_running)
        self.assertEqual(len(pm.all_status()), 3)

        pm.stop_all(timeout=1)
        self.assertFalse(pm.is_running)

        import shutil
        shutil.rmtree(vault, ignore_errors=True)
