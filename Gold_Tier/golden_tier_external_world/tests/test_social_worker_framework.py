from unittest import TestCase
from unittest.mock import Mock, patch
from datetime import datetime, timezone
import tempfile
import shutil
import time
from pathlib import Path

from golden_tier_external_world.workers.social.status import WorkerStatus, StatusTracker
from golden_tier_external_world.workers.social.queue import TaskQueue, TaskPriority, Task
from golden_tier_external_world.workers.social.scheduler import Scheduler
from golden_tier_external_world.workers.social.base import BaseSocialWorker
from golden_tier_external_world.config.enums import EventType, PlatformType, ContentCategory
from golden_tier_external_world.events.models import BaseEvent, MessageEvent
from golden_tier_external_world.events.bus import LocalEventBus
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.storage.interface import StorageInterface


class DummyStorage(StorageInterface):
    def __init__(self) -> None:
        self._processed: set[str] = set()
        self._states: dict[str, object] = {}
        self._poll_times: dict[str, str] = {}
        self._events: list[BaseEvent] = []
    def save_event(self, event: BaseEvent) -> None:
        self._events.append(event)
    def get_events(self, platform=None, event_type=None, limit=100) -> list[BaseEvent]:
        return self._events[:limit]
    def is_processed(self, event_id: str) -> bool:
        return event_id in self._processed
    def mark_processed(self, event_id: str) -> None:
        self._processed.add(event_id)
    def get_last_poll_time(self, platform):
        return self._poll_times.get(platform.value if isinstance(platform, object) else platform)
    def set_last_poll_time(self, platform, timestamp):
        self._poll_times[platform.value] = timestamp
    def save_state(self, platform, key, value):
        self._states[f"{platform.value}/{key}"] = value
    def load_state(self, platform, key):
        return self._states.get(f"{platform.value}/{key}")


# ── StatusTracker tests ──────────────────────────────────────

class TestStatusTracker(TestCase):
    def setUp(self) -> None:
        self.tracker = StatusTracker()

    def test_initial_status_is_idle(self) -> None:
        self.assertEqual(self.tracker.status, WorkerStatus.IDLE)

    def test_set_running_updates_uptime(self) -> None:
        self.assertIsNone(self.tracker.uptime)
        self.tracker.status = WorkerStatus.RUNNING
        self.assertIsNotNone(self.tracker.uptime)
        self.assertTrue(self.tracker.is_running)

    def test_record_success_increments_count(self) -> None:
        self.tracker.record_success()
        snap = self.tracker.snapshot()
        self.assertEqual(snap["processed_count"], 1)
        self.assertEqual(snap["failed_count"], 0)

    def test_record_failure_increments_count(self) -> None:
        self.tracker.record_failure(error="test error")
        snap = self.tracker.snapshot()
        self.assertEqual(snap["failed_count"], 1)
        self.assertEqual(snap["last_error"], "test error")

    def test_reset_clears_state(self) -> None:
        self.tracker.status = WorkerStatus.RUNNING
        self.tracker.record_success()
        self.tracker.record_failure()
        self.tracker.reset()
        snap = self.tracker.snapshot()
        self.assertEqual(snap["status"], "IDLE")
        self.assertEqual(snap["processed_count"], 0)
        self.assertEqual(snap["failed_count"], 0)
        self.assertIsNone(snap["last_error"])

    def test_stopped_status(self) -> None:
        self.tracker.status = WorkerStatus.STOPPED
        self.assertTrue(self.tracker.is_stopped)
        self.tracker.status = WorkerStatus.ERROR
        self.assertTrue(self.tracker.is_stopped)

    def test_paused_not_running(self) -> None:
        self.tracker.status = WorkerStatus.PAUSED
        self.assertFalse(self.tracker.is_running)

    def test_idle_not_running(self) -> None:
        self.assertFalse(self.tracker.is_running)


# ── TaskQueue tests ───────────────────────────────────────────

class TestTaskQueue(TestCase):
    def setUp(self) -> None:
        self.queue = TaskQueue()

    def test_enqueue_and_dequeue(self) -> None:
        handler = Mock(return_value="ok")
        tid = self.queue.enqueue(handler, 1, key="val")
        self.assertIsNotNone(tid)
        self.assertEqual(self.queue.pending_count, 1)

        task = self.queue.dequeue(timeout=0.5)
        self.assertIsNotNone(task)
        self.assertEqual(task.task_id, tid)
        self.assertEqual(self.queue.processing_count, 1)
        self.assertEqual(self.queue.pending_count, 0)

    def test_ack_completes_task(self) -> None:
        handler = Mock()
        tid = self.queue.enqueue(handler)
        task = self.queue.dequeue(timeout=0.5)
        result = self.queue.ack(tid, result="done")
        self.assertTrue(result)
        self.assertEqual(self.queue.processing_count, 0)

    def test_nack_requeues_task(self) -> None:
        handler = Mock()
        tid = self.queue.enqueue(handler)
        self.queue.dequeue(timeout=0.5)
        self.queue.nack(tid, requeue=True, error="fail")
        self.assertEqual(self.queue.pending_count, 1)
        self.assertEqual(self.queue.processing_count, 0)

    def test_nack_exhausted_does_not_requeue(self) -> None:
        handler = Mock()
        tid = self.queue.enqueue(handler, max_retries=0)
        self.queue.dequeue(timeout=0.5)
        self.queue.nack(tid, requeue=True, error="fail")
        self.assertEqual(self.queue.pending_count, 0)

    def test_task_priority_order(self) -> None:
        results: list[str] = []
        handler = lambda x: results.append(x)
        self.queue.enqueue(handler, "low", priority=TaskPriority.LOW)
        self.queue.enqueue(handler, "high", priority=TaskPriority.HIGH)
        self.queue.enqueue(handler, "critical", priority=TaskPriority.CRITICAL)

        task1 = self.queue.dequeue(timeout=0.5)
        task2 = self.queue.dequeue(timeout=0.5)
        task3 = self.queue.dequeue(timeout=0.5)

        self.assertEqual(task1.kwargs, {})  # no kwargs
        self.assertEqual(task1.args[0], "critical")
        self.assertEqual(task2.args[0], "high")
        self.assertEqual(task3.args[0], "low")

    def test_clear_empties_queue(self) -> None:
        handler = Mock()
        self.queue.enqueue(handler)
        self.queue.enqueue(handler)
        cleared = self.queue.clear()
        self.assertEqual(cleared, 2)
        self.assertEqual(self.queue.pending_count, 0)

    def test_retry_later(self) -> None:
        handler = Mock()
        tid = self.queue.enqueue(handler, max_retries=3)
        self.queue.dequeue(timeout=0.5)

        before = self.queue.pending_count
        self.queue.retry_later(tid, delay_seconds=0.01, error="try again")
        self.assertEqual(self.queue.processing_count, 0)

        time.sleep(0.05)
        task = self.queue.dequeue(timeout=0.5)
        self.assertIsNotNone(task)

    def test_dequeue_timeout_returns_none(self) -> None:
        task = self.queue.dequeue(timeout=0.1)
        self.assertIsNone(task)


# ── Scheduler tests ───────────────────────────────────────────

class TestScheduler(TestCase):
    def setUp(self) -> None:
        self.scheduler = Scheduler()

    def tearDown(self) -> None:
        self.scheduler.stop()

    def test_interval_task_executes(self) -> None:
        handler = Mock()
        self.scheduler.add_interval("t1", handler, interval_seconds=0.05)
        self.scheduler.start()
        time.sleep(0.3)
        self.assertGreater(handler.call_count, 0)

    def test_delayed_task_executes_once(self) -> None:
        handler = Mock()
        self.scheduler.add_delayed("t2", handler, delay_seconds=0.05)
        self.scheduler.start()
        time.sleep(0.3)
        self.assertEqual(handler.call_count, 1)

    def test_remove_task(self) -> None:
        handler = Mock()
        self.scheduler.add_interval("t3", handler, interval_seconds=0.05)
        self.scheduler.remove("t3")
        self.scheduler.start()
        time.sleep(0.2)
        handler.assert_not_called()

    def test_disable_task(self) -> None:
        handler = Mock()
        self.scheduler.add_interval("t4", handler, interval_seconds=0.05)
        self.scheduler.disable("t4")
        self.scheduler.start()
        time.sleep(0.2)
        handler.assert_not_called()

    def test_max_runs_honored(self) -> None:
        handler = Mock()
        self.scheduler.add_interval("t5", handler, interval_seconds=0.05, max_runs=2)
        self.scheduler.start()
        time.sleep(0.4)
        self.assertLessEqual(handler.call_count, 2)

    def test_list_tasks(self) -> None:
        self.scheduler.add_interval("t6", Mock(), interval_seconds=10)
        tasks = self.scheduler.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "t6")
        self.assertEqual(tasks[0]["interval_seconds"], 10)


# ── BaseSocialWorker tests ────────────────────────────────────

class ConcreteWorker(BaseSocialWorker):
    @property
    def event_type(self) -> EventType:
        return EventType.MESSAGE

    def _execute(self, event: BaseEvent) -> dict:
        return {"status": "processed", "event_id": event.event_id}


class TestBaseSocialWorker(TestCase):
    def setUp(self) -> None:
        self.storage = DummyStorage()
        self.bus = LocalEventBus()
        self.worker = ConcreteWorker(
            storage=self.storage,
            event_bus=self.bus,
            max_retries=2,
            backoff_base=1.0,
            backoff_max=1.0,
            backoff_jitter=0.0,
            health_check_interval=60.0,
        )

    def tearDown(self) -> None:
        self.worker.graceful_shutdown(timeout=2)

    def test_initial_status(self) -> None:
        self.assertEqual(self.worker.worker_status, WorkerStatus.IDLE)

    def test_start_sets_running(self) -> None:
        self.worker.start()
        self.assertEqual(self.worker.worker_status, WorkerStatus.RUNNING)

    def test_stop_sets_stopped(self) -> None:
        self.worker.start()
        self.worker.stop()
        self.assertEqual(self.worker.worker_status, WorkerStatus.STOPPED)

    def test_pause_and_resume(self) -> None:
        self.worker.start()
        self.worker.pause()
        self.assertEqual(self.worker.worker_status, WorkerStatus.PAUSED)
        self.worker.resume()
        self.assertEqual(self.worker.worker_status, WorkerStatus.RUNNING)

    def test_process_event_through_queue(self) -> None:
        self.worker.start()
        sender = PlatformAccount(
            platform=PlatformType.LINKEDIN,
            account_id="u1",
            display_name="Alice",
            username="alice",
        )
        content = ContentItem(
            content_id="c1",
            platform=PlatformType.LINKEDIN,
            content_type=ContentCategory.TEXT,
            text="Hello",
        )
        event = MessageEvent(
            event_id="test_evt_1",
            platform=PlatformType.LINKEDIN,
            timestamp=datetime.now(timezone.utc),
            sender=sender,
            content=content,
            conversation_id="conv1",
        )

        result = self.worker.process(event)
        self.assertEqual(result["status"], "queued")

        time.sleep(0.5)

        snap = self.worker.status_snapshot
        self.assertGreaterEqual(snap["processed_count"], 1)

    def test_task_queue_integration(self) -> None:
        handler = Mock(return_value="done")
        tid = self.worker.task_queue.enqueue(handler, "arg1")
        task = self.worker.task_queue.dequeue(timeout=0.5)
        self.assertIsNotNone(task)
        self.assertEqual(task.task_id, tid)
        self.assertEqual(task.args[0], "arg1")

    def test_scheduler_integration(self) -> None:
        handler = Mock()
        self.worker.scheduler.add_delayed("st1", handler, delay_seconds=0.05)
        self.worker.scheduler.start()
        time.sleep(0.3)
        self.assertEqual(handler.call_count, 1)

    def test_consecutive_failures_triggers_error(self) -> None:
        self.worker._consecutive_failures = 10
        self.worker._max_consecutive_failures = 5
        result = self.worker._health_check()
        self.assertFalse(result)
        self.assertEqual(self.worker.worker_status, WorkerStatus.ERROR)

    def test_uptime_after_start(self) -> None:
        self.assertIsNone(self.worker.uptime)
        self.worker.start()
        self.assertIsNotNone(self.worker.uptime)
        self.assertGreaterEqual(self.worker.uptime, 0)

    def test_status_snapshot(self) -> None:
        snap = self.worker.status_snapshot
        self.assertIn("status", snap)
        self.assertIn("processed_count", snap)
        self.assertIn("failed_count", snap)
