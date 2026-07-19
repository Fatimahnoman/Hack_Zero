from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest import TestCase, mock
import json
import os
import tempfile

from golden_tier_external_world.config.enums import ContentCategory, EventType, PlatformType
from golden_tier_external_world.events.models import (
    BaseEvent,
    MessageEvent,
    CommentEvent,
    MentionEvent,
)
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.posters.base import PostContent
from golden_tier_external_world.content_orchestrator.generator import ContentGenerator
from golden_tier_external_world.content_orchestrator.dedup import ContentDedup
from golden_tier_external_world.content_orchestrator.queue import (
    ContentQueue,
    ContentQueueItem,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_IN_PROGRESS,
    QUEUE_STATUS_DONE,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_DLQ,
)
from golden_tier_external_world.content_orchestrator.engine import (
    ContentEngine,
    _RESPOND_EVENT_TYPES,
    _TARGET_PLATFORMS,
)
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.monitoring.metrics import MetricsCollector


def _make_event(
    event_type: EventType = EventType.MESSAGE,
    platform: PlatformType = PlatformType.FACEBOOK,
    text: str = "Hello!",
) -> MessageEvent:
    return MessageEvent(
        platform=platform,
        timestamp=datetime.now(timezone.utc),
        sender=PlatformAccount(
            platform=platform,
            platform_id="user_123",
            username="testuser",
            display_name="Test User",
            account_id="acc_123",
        ),
        content=ContentItem(
            content_id="ci_1",
            platform=platform,
            content_type=ContentCategory.TEXT,
            text=text,
        ),
        conversation_id="conv_1",
    )


def _make_tmp_backend() -> JsonBackend:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    return JsonBackend(Path(path))


# ── ContentGenerator Tests ──────────────────────────────────


class TestContentGenerator(TestCase):
    def setUp(self) -> None:
        self.generator = ContentGenerator()

    def test_generates_template_for_message(self) -> None:
        event = _make_event()
        content = self.generator.generate(event, PlatformType.FACEBOOK)
        assert content is not None
        self.assertIn("Test User", content.text)

    def test_generates_template_for_twitter(self) -> None:
        event = _make_event()
        content = self.generator.generate(event, PlatformType.TWITTER)
        assert content is not None
        self.assertTrue(len(content.text) <= 280)

    def test_generates_template_for_instagram(self) -> None:
        event = _make_event()
        content = self.generator.generate(event, PlatformType.INSTAGRAM)
        assert content is not None
        self.assertIn("Test User", content.text)

    def test_returns_none_for_unknown_event_type(self) -> None:
        event = mock.MagicMock(spec=BaseEvent)
        event.event_type = EventType.LIKE
        event.event_id = "evt_mock"
        content = self.generator.generate(event, PlatformType.FACEBOOK)
        self.assertIsNone(content)

    def test_custom_generator_fn(self) -> None:
        def custom_gen(event: BaseEvent, platform: PlatformType) -> Optional[str]:
            return f"Custom response for {platform.value}"

        generator = ContentGenerator(generator_fn=custom_gen)
        event = _make_event()
        content = generator.generate(event, PlatformType.FACEBOOK)
        assert content is not None
        self.assertEqual(content.text, "Custom response for facebook")

    def test_custom_generator_fallback_on_error(self) -> None:
        def failing_gen(event: BaseEvent, platform: PlatformType) -> Optional[str]:
            raise ValueError("oops")

        generator = ContentGenerator(generator_fn=failing_gen)
        event = _make_event()
        content = generator.generate(event, PlatformType.FACEBOOK)
        assert content is not None
        self.assertIn("Test User", content.text)

    def test_comment_event_template(self) -> None:
        event = CommentEvent(
            platform=PlatformType.INSTAGRAM,
            timestamp=datetime.now(timezone.utc),
            author=PlatformAccount(
                platform=PlatformType.INSTAGRAM,
                platform_id="user_456",
                username="commenter",
                display_name="Commenter",
                account_id="acc_456",
            ),
            content=ContentItem(
                content_id="ci_2",
                platform=PlatformType.INSTAGRAM,
                content_type=ContentCategory.TEXT,
                text="Nice post!",
            ),
            parent_post_id="post_1",
        )
        content = self.generator.generate(event, PlatformType.INSTAGRAM)
        assert content is not None
        self.assertIn("comment", content.text.lower())

    def test_mention_event_template(self) -> None:
        event = MentionEvent(
            platform=PlatformType.TWITTER,
            timestamp=datetime.now(timezone.utc),
            mentioned_by=PlatformAccount(
                platform=PlatformType.TWITTER,
                platform_id="user_789",
                username="mentioner",
                display_name="Mentioner",
                account_id="acc_789",
            ),
            content=ContentItem(
                content_id="ci_3",
                platform=PlatformType.TWITTER,
                content_type=ContentCategory.TEXT,
                text="Check this out @us",
            ),
            source_url="https://x.com/status/123",
        )
        content = self.generator.generate(event, PlatformType.TWITTER)
        assert content is not None
        self.assertIn("mention", content.text.lower())


# ── ContentDedup Tests ──────────────────────────────────────


class TestContentDedup(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.vault = SeenVault(self.backend)
        self.dedup = ContentDedup(self.vault)

    def tearDown(self) -> None:
        try:
            os.unlink(self.backend.path)
        except OSError:
            pass

    def test_not_posted_initially(self) -> None:
        event = _make_event()
        self.assertFalse(self.dedup.is_posted(event, PlatformType.FACEBOOK))

    def test_mark_posted_then_is_posted(self) -> None:
        event = _make_event()
        self.dedup.mark_posted(event, PlatformType.FACEBOOK, post_id="fb_post_1")
        self.assertTrue(self.dedup.is_posted(event, PlatformType.FACEBOOK))

    def test_different_platform_independent(self) -> None:
        event = _make_event()
        self.dedup.mark_posted(event, PlatformType.FACEBOOK)
        self.assertFalse(self.dedup.is_posted(event, PlatformType.TWITTER))
        self.assertTrue(self.dedup.is_posted(event, PlatformType.FACEBOOK))

    def test_different_event_independent(self) -> None:
        event1 = _make_event(text="Hello")
        event2 = _make_event(text="World")
        self.dedup.mark_posted(event1, PlatformType.FACEBOOK)
        self.assertFalse(self.dedup.is_posted(event2, PlatformType.FACEBOOK))

    def test_mark_posted_batch(self) -> None:
        event = _make_event()
        self.dedup.mark_posted_batch(event, [PlatformType.FACEBOOK, PlatformType.TWITTER])
        self.assertTrue(self.dedup.is_posted(event, PlatformType.FACEBOOK))
        self.assertTrue(self.dedup.is_posted(event, PlatformType.TWITTER))
        self.assertFalse(self.dedup.is_posted(event, PlatformType.INSTAGRAM))

    def test_clear(self) -> None:
        event = _make_event()
        self.dedup.mark_posted(event, PlatformType.FACEBOOK)
        self.dedup.clear()
        self.assertFalse(self.dedup.is_posted(event, PlatformType.FACEBOOK))


# ── ContentQueue Tests ──────────────────────────────────────


class TestContentQueue(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.queue = ContentQueue(self.backend)

    def tearDown(self) -> None:
        try:
            os.unlink(self.backend.path)
        except OSError:
            pass

    def test_enqueue_creates_item(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1",
            platform="facebook",
            text="Hello world",
        )
        self.assertTrue(len(item_id) > 0)
        self.assertEqual(self.queue.pending_count(), 1)

    def test_dequeue_returns_item(self) -> None:
        self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="Hello")
        item = self.queue.dequeue()
        assert item is not None
        self.assertEqual(item.text, "Hello")
        self.assertEqual(item.status, QUEUE_STATUS_IN_PROGRESS)

    def test_dequeue_by_platform(self) -> None:
        self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="FB")
        self.queue.enqueue(source_event_id="evt_1", platform="twitter", text="TW")
        fb_item = self.queue.dequeue(platform="facebook")
        assert fb_item is not None
        self.assertEqual(fb_item.platform, "facebook")
        tw_item = self.queue.dequeue(platform="twitter")
        assert tw_item is not None
        self.assertEqual(tw_item.platform, "twitter")

    def test_dequeue_empty(self) -> None:
        item = self.queue.dequeue()
        self.assertIsNone(item)

    def test_ack_marks_done(self) -> None:
        item_id = self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="Hi")
        self.queue.dequeue()
        self.queue.ack(item_id, post_id="post_abc")
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_DONE)
        self.assertEqual(item.post_id, "post_abc")

    def test_nack_requeues(self) -> None:
        item_id = self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="Hi")
        self.queue.dequeue()
        self.queue.nack(item_id, error="Timeout", requeue=True)
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_PENDING)
        self.assertEqual(item.retry_count, 1)
        self.assertEqual(item.error, "Timeout")

    def test_nack_exhausted(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1",
            platform="facebook",
            text="Hi",
            max_retries=2,
        )
        self.queue.dequeue()
        self.queue.nack(item_id, error="e1", requeue=True)
        self.queue.dequeue()
        self.queue.nack(item_id, error="e2", requeue=True)
        self.queue.dequeue()
        self.queue.nack(item_id, error="e3", requeue=True)
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_DLQ)
        self.assertEqual(item.retry_count, 3)

    def test_nack_discard(self) -> None:
        item_id = self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="Hi")
        self.queue.dequeue()
        self.queue.nack(item_id, error="Fatal", requeue=False)
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_FAILED)

    def test_pending_count_by_platform(self) -> None:
        self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="A")
        self.queue.enqueue(source_event_id="evt_1", platform="twitter", text="B")
        self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="C")
        self.assertEqual(self.queue.pending_count(platform="facebook"), 2)
        self.assertEqual(self.queue.pending_count(platform="twitter"), 1)

    def test_failed_count(self) -> None:
        item_id = self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="X")
        self.queue.dequeue()
        self.queue.nack(item_id, error="Fail", requeue=False)
        self.assertEqual(self.queue.failed_count(platform="facebook"), 1)

    def test_clear_done(self) -> None:
        item_id = self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="Y")
        self.queue.dequeue()
        self.queue.ack(item_id)
        cleared = self.queue.clear_done()
        self.assertEqual(cleared, 1)
        self.assertEqual(len(self.queue._items), 0)

    def test_persistence(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1",
            platform="twitter",
            text="Persist me!",
        )

        queue2 = ContentQueue(self.backend)
        self.assertEqual(queue2.pending_count(), 1)
        item = queue2.dequeue()
        assert item is not None
        self.assertEqual(item.text, "Persist me!")

    def test_all_items(self) -> None:
        self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="A")
        self.queue.enqueue(source_event_id="evt_1", platform="twitter", text="B")
        items = self.queue.all_items()
        self.assertEqual(len(items), 2)


# ── ContentEngine Tests ─────────────────────────────────────


class TestContentEngine(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.vault = SeenVault(self.backend)
        self.dedup = ContentDedup(self.vault)
        self.queue_backend = _make_tmp_backend()
        self.queue = ContentQueue(self.queue_backend)
        self.generator = ContentGenerator()
        self.metrics = MetricsCollector()

        self.mock_pm = mock.MagicMock()
        self.mock_pm.send_event.return_value = {
            "status": "ok",
            "data": {"post_id": "post_abc"},
        }

        self.engine = ContentEngine(
            storage=mock.MagicMock(),
            event_bus=mock.MagicMock(),
            metrics=self.metrics,
            generator=self.generator,
            dedup=self.dedup,
            queue=self.queue,
            process_manager=self.mock_pm,
        )
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        for p in [self.backend.path, self.queue_backend.path]:
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_process_message_event_enqueues_for_all_platforms(self) -> None:
        event = _make_event()
        result = self.engine.process_event_sync(event)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["enqueued_platforms"]), 3)
        self.assertIn("facebook", result["enqueued_platforms"])
        self.assertIn("twitter", result["enqueued_platforms"])
        self.assertIn("instagram", result["enqueued_platforms"])

    def test_skips_unsupported_event_type(self) -> None:
        event = mock.MagicMock(spec=BaseEvent)
        event.event_type = EventType.LIKE
        event.event_id = "test_skip"
        event.platform = PlatformType.FACEBOOK
        result = self.engine.process_event_sync(event)
        self.assertEqual(result["status"], "skipped")

    def test_dedup_prevents_duplicate_enqueue(self) -> None:
        event = _make_event()
        first = self.engine.process_event_sync(event)
        self.assertEqual(first["status"], "ok")

        second = self.engine.process_event_sync(event)
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(len(second["enqueued_platforms"]), 0)

    def test_processor_sends_to_platform(self) -> None:
        event = _make_event()
        self.engine.process_event_sync(event)

        self.engine._process_one_item()
        self.engine._process_one_item()
        self.engine._process_one_item()

        self.assertEqual(self.mock_pm.send_event.call_count, 3)

    def test_processor_acks_on_success(self) -> None:
        event = _make_event()
        self.engine.process_event_sync(event)
        self.engine._process_one_item()
        self.engine._process_one_item()
        self.engine._process_one_item()

        item = self.queue.dequeue()
        self.assertIsNone(item)

    def test_processor_nack_on_failure(self) -> None:
        self.mock_pm.send_event.return_value = None
        event = _make_event()
        with mock.patch.object(self.engine, "_target_platforms", [PlatformType.FACEBOOK]):
            self.engine.process_event_sync(event)
        self.engine._process_one_item()

        self.assertEqual(self.queue.pending_count(), 1)

    def test_processor_nack_on_exception(self) -> None:
        self.mock_pm.send_event.side_effect = RuntimeError("Connection lost")
        event = _make_event()
        with mock.patch.object(self.engine, "_target_platforms", [PlatformType.FACEBOOK]):
            self.engine.process_event_sync(event)
        self.engine._process_one_item()

        self.assertEqual(self.queue.pending_count(), 1)

    def test_processor_nack_on_error_status(self) -> None:
        self.mock_pm.send_event.return_value = {"status": "error", "error": "Auth failed"}
        event = _make_event()
        with mock.patch.object(self.engine, "_target_platforms", [PlatformType.FACEBOOK]):
            self.engine.process_event_sync(event)
        self.engine._process_one_item()

        self.assertEqual(self.queue.pending_count(), 1)

    def test_content_generated_for_each_platform(self) -> None:
        event = _make_event()
        result = self.engine.process_event_sync(event)
        self.assertEqual(len(result["enqueued_platforms"]), 3)

        items = self.queue.all_items()
        platforms = [item.platform for item in items]
        self.assertIn("facebook", platforms)
        self.assertIn("twitter", platforms)
        self.assertIn("instagram", platforms)

    def test_metrics_recorded_for_posts(self) -> None:
        event = _make_event()
        self.engine.process_event_sync(event)
        self.engine._process_one_item()
        self.engine._process_one_item()
        self.engine._process_one_item()

        snapshot = self.metrics.post_snapshot()
        self.assertIn("post:facebook:ok", snapshot)
        self.assertIn("post:twitter:ok", snapshot)
        self.assertIn("post:instagram:ok", snapshot)

    def test_metrics_recorded_for_failures(self) -> None:
        self.mock_pm.send_event.return_value = None
        event = _make_event()
        self.engine.process_event_sync(event)
        self.engine._process_one_item()

        snapshot = self.metrics.post_snapshot()
        has_failure = any("failed" in k for k in snapshot)
        self.assertTrue(has_failure)


class TestContentEngineLifecycle(TestCase):
    def test_start_stop(self) -> None:
        backend = _make_tmp_backend()
        queue_backend = _make_tmp_backend()
        engine = ContentEngine(
            storage=mock.MagicMock(),
            event_bus=mock.MagicMock(),
            metrics=MetricsCollector(),
            generator=ContentGenerator(),
            dedup=ContentDedup(SeenVault(backend)),
            queue=ContentQueue(queue_backend),
            process_manager=mock.MagicMock(),
        )

        self.assertFalse(engine.is_running)
        engine.start()
        self.assertTrue(engine.is_running)
        engine.stop()
        self.assertFalse(engine.is_running)

        for p in [backend.path, queue_backend.path]:
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_register_subscribes_to_event_types(self) -> None:
        mock_bus = mock.MagicMock()
        engine = ContentEngine(
            storage=mock.MagicMock(),
            event_bus=mock_bus,
            metrics=MetricsCollector(),
            generator=ContentGenerator(),
            dedup=ContentDedup(SeenVault(_make_tmp_backend())),
            queue=ContentQueue(_make_tmp_backend()),
            process_manager=mock.MagicMock(),
        )
        engine.register()
        self.assertEqual(mock_bus.subscribe.call_count, len(_RESPOND_EVENT_TYPES))

    def test_queue_property(self) -> None:
        q = ContentQueue(_make_tmp_backend())
        engine = ContentEngine(
            storage=mock.MagicMock(),
            event_bus=mock.MagicMock(),
            metrics=MetricsCollector(),
            generator=ContentGenerator(),
            dedup=ContentDedup(SeenVault(_make_tmp_backend())),
            queue=q,
            process_manager=mock.MagicMock(),
        )
        self.assertIs(engine.queue, q)


class TestContentEngineWithPlanner(TestCase):
    @mock.patch("golden_tier_external_world.planner.ContentEngine")
    def test_planner_calls_content_engine(self, mock_engine_cls: mock.MagicMock) -> None:
        from golden_tier_external_world.planner import Planner

        mock_engine = mock.MagicMock()
        mock_engine.process_event_sync.return_value = {"status": "ok", "enqueued_platforms": ["facebook"]}

        planner = Planner(
            storage=mock.MagicMock(),
            event_bus=mock.MagicMock(),
            metrics=MetricsCollector(),
            content_engine=mock_engine,
        )

        event = _make_event()
        result = planner._on_event(event)

        mock_engine.process_event_sync.assert_called_once_with(event)
        self.assertIn("content", result)
        self.assertEqual(result["content"]["status"], "ok")

    @mock.patch("golden_tier_external_world.planner.ContentEngine")
    def test_planner_handles_content_engine_error(self, mock_engine_cls: mock.MagicMock) -> None:
        from golden_tier_external_world.planner import Planner

        mock_engine = mock.MagicMock()
        mock_engine.process_event_sync.side_effect = RuntimeError("Engine failure")

        planner = Planner(
            storage=mock.MagicMock(),
            event_bus=mock.MagicMock(),
            metrics=MetricsCollector(),
            content_engine=mock_engine,
        )

        event = _make_event()
        result = planner._on_event(event)

        self.assertIn("content", result)
        self.assertEqual(result["content"]["status"], "error")
