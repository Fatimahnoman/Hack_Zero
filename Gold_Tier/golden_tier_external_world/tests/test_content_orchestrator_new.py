
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from unittest import TestCase, mock
import json
import os
import tempfile
import time

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
from golden_tier_external_world.content_orchestrator.prompts import PromptManager, PromptTemplate
from golden_tier_external_world.content_orchestrator.validator import ContentValidator, ValidationResult
from golden_tier_external_world.content_orchestrator.rate_limiter import RateLimiter
from golden_tier_external_world.content_orchestrator.post_result import PostResult
from golden_tier_external_world.content_orchestrator.queue import (
    ContentQueue,
    ContentQueueItem,
    QUEUE_STATUS_PENDING,
    QUEUE_STATUS_IN_PROGRESS,
    QUEUE_STATUS_DONE,
    QUEUE_STATUS_FAILED,
    QUEUE_STATUS_DLQ,
    QUEUE_STATUS_SCHEDULED,
    QUEUE_PRIORITY_CRITICAL,
    QUEUE_PRIORITY_HIGH,
    QUEUE_PRIORITY_MEDIUM,
    QUEUE_PRIORITY_LOW,
)
from golden_tier_external_world.content_orchestrator.generator import ContentGenerator
from golden_tier_external_world.content_orchestrator.dedup import ContentDedup
from golden_tier_external_world.content_orchestrator.engine import ContentEngine
from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault


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


# ── PromptManager Tests ────────────────────────────────────


class TestPromptManager(TestCase):
    def setUp(self) -> None:
        self.pm = PromptManager()

    def test_default_templates_loaded(self) -> None:
        self.assertGreater(self.pm.registered_count, 0)

    def test_get_system_prompt_for_message_facebook(self) -> None:
        prompt = self.pm.get_system_prompt(EventType.MESSAGE, PlatformType.FACEBOOK)
        self.assertIsNotNone(prompt)
        self.assertIn("Facebook", prompt)

    def test_get_system_prompt_for_message_twitter(self) -> None:
        prompt = self.pm.get_system_prompt(EventType.MESSAGE, PlatformType.TWITTER)
        self.assertIsNotNone(prompt)
        self.assertIn("280", prompt)

    def test_get_system_prompt_returns_none_for_unknown(self) -> None:
        prompt = self.pm.get_system_prompt(EventType.LIKE, PlatformType.FACEBOOK)
        self.assertIsNone(prompt)

    def test_register_template(self) -> None:
        tmpl = PromptTemplate(
            template="Custom: {sender_name} said {message_text}",
            event_type=EventType.MESSAGE,
            platform=PlatformType.FACEBOOK,
            language="en",
            tone="professional",
        )
        self.pm.register_template(tmpl)
        prompt = self.pm.get_system_prompt(
            EventType.MESSAGE, PlatformType.FACEBOOK, tone="professional",
        )
        self.assertIsNotNone(prompt)
        self.assertIn("Custom:", prompt)

    def test_set_override(self) -> None:
        self.pm.set_override(EventType.MESSAGE, PlatformType.FACEBOOK, "OVERRIDE PROMPT")
        prompt = self.pm.get_system_prompt(EventType.MESSAGE, PlatformType.FACEBOOK)
        self.assertEqual(prompt, "OVERRIDE PROMPT")

    def test_format_prompt(self) -> None:
        event = _make_event()
        formatted = self.pm.format_prompt(
            EventType.MESSAGE, PlatformType.FACEBOOK, event,
        )
        self.assertIsNotNone(formatted)
        self.assertIn("Test User", formatted)
        self.assertIn("Hello!", formatted)

    def test_build_user_content(self) -> None:
        event = _make_event()
        content = self.pm.build_user_content(event)
        self.assertIn("Test User", content)
        self.assertIn("Hello!", content)


# ── ContentValidator Tests ─────────────────────────────────


class TestContentValidator(TestCase):
    def setUp(self) -> None:
        self.validator = ContentValidator()

    def test_valid_content_passes(self) -> None:
        content = PostContent(text="Hello, this is a valid post!")
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.errors), 0)

    def test_empty_text_fails(self) -> None:
        content = PostContent(text="")
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertFalse(result.valid)
        self.assertIn("empty", result.errors[0].lower())

    def test_max_length_exceeded_twitter(self) -> None:
        long_text = "x" * 300
        content = PostContent(text=long_text)
        result = self.validator.validate(content, PlatformType.TWITTER)
        self.assertFalse(result.valid)
        self.assertIn("280", result.errors[0])

    def test_unsafe_content_detected(self) -> None:
        content = PostContent(text="This is a spam message")
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertFalse(result.valid)
        self.assertIn("unsafe", result.errors[0].lower())

    def test_duplicate_hashtags_detected(self) -> None:
        content = PostContent(text="Check this out #hello #world #hello")
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertFalse(result.valid)
        self.assertIn("Duplicate hashtags", result.errors[0])

    def test_duplicate_emojis_detected(self) -> None:
        content = PostContent(text="Great post! 😊😊")
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertFalse(result.valid)
        self.assertIn("Duplicate emojis", result.errors[0])

    def test_media_not_found_warns(self) -> None:
        content = PostContent(text="Post with media", media_paths=[Path("nonexistent.jpg")])
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertTrue(result.valid)
        self.assertGreater(len(result.warnings), 0)

    def test_unsupported_media_format_warns(self) -> None:
        content = PostContent(text="Post", media_paths=[Path("file.txt")])
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertTrue(result.valid)
        has_warning = any("Unsupported media" in w for w in result.warnings)
        self.assertTrue(has_warning)

    def test_configure_max_length(self) -> None:
        self.validator.set_max_length(PlatformType.FACEBOOK, 10)
        content = PostContent(text="This is too long for the custom limit")
        result = self.validator.validate(content, PlatformType.FACEBOOK)
        self.assertFalse(result.valid)

    def test_disabled_checks(self) -> None:
        v = ContentValidator(
            check_unsafe=False,
            check_duplicate_hashtags=False,
            check_duplicate_emojis=False,
            check_repeated_sentences=False,
            check_urls=False,
            check_media=False,
        )
        content = PostContent(text="spam with #hash #hash 😊😊")
        result = v.validate(content, PlatformType.TWITTER)
        self.assertTrue(result.valid)


# ── RateLimiter Tests ───────────────────────────────────────


class TestRateLimiter(TestCase):
    def setUp(self) -> None:
        self.limiter = RateLimiter()

    def test_initial_allow(self) -> None:
        self.assertTrue(self.limiter.allow(PlatformType.FACEBOOK))

    def test_remaining_starts_at_max(self) -> None:
        remaining = self.limiter.remaining(PlatformType.FACEBOOK)
        self.assertEqual(remaining, 200)

    def test_consumption_decreases_remaining(self) -> None:
        self.limiter.consume(PlatformType.FACEBOOK)
        self.assertEqual(self.limiter.remaining(PlatformType.FACEBOOK), 199)

    def test_limit_exceeded(self) -> None:
        limiter = RateLimiter(limits={
            PlatformType.FACEBOOK: (2, 86400.0),
        })
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))
        self.assertFalse(limiter.allow(PlatformType.FACEBOOK))

    def test_reset_platform(self) -> None:
        limiter = RateLimiter(limits={
            PlatformType.FACEBOOK: (1, 86400.0),
        })
        limiter.consume(PlatformType.FACEBOOK)
        self.assertFalse(limiter.allow(PlatformType.FACEBOOK))
        limiter.reset(PlatformType.FACEBOOK)
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))

    def test_reset_all(self) -> None:
        limiter = RateLimiter(limits={
            PlatformType.FACEBOOK: (1, 86400.0),
            PlatformType.TWITTER: (1, 86400.0),
        })
        limiter.consume(PlatformType.FACEBOOK)
        limiter.consume(PlatformType.TWITTER)
        limiter.reset()
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))
        self.assertTrue(limiter.allow(PlatformType.TWITTER))

    def test_set_limit_dynamically(self) -> None:
        self.limiter.set_limit(PlatformType.FACEBOOK, 5, 60.0)
        for _ in range(5):
            self.assertTrue(self.limiter.allow(PlatformType.FACEBOOK))
        self.assertFalse(self.limiter.allow(PlatformType.FACEBOOK))

    def test_limits_report(self) -> None:
        limits = self.limiter.limits()
        self.assertIn(PlatformType.FACEBOOK, limits)
        self.assertIn("max_calls", limits[PlatformType.FACEBOOK])
        self.assertIn("remaining", limits[PlatformType.FACEBOOK])

    def test_sliding_window_expires(self) -> None:
        limiter = RateLimiter(limits={
            PlatformType.FACEBOOK: (2, 0.05),
        })
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))
        self.assertFalse(limiter.allow(PlatformType.FACEBOOK))
        time.sleep(0.06)
        self.assertTrue(limiter.allow(PlatformType.FACEBOOK))


# ── PostResult Tests ────────────────────────────────────────


class TestPostResult(TestCase):
    def test_default_creation(self) -> None:
        result = PostResult(platform="facebook")
        self.assertEqual(result.platform, "facebook")
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.retry_count, 0)
        self.assertIsNotNone(result.result_id)

    def test_full_creation(self) -> None:
        result = PostResult(
            platform="twitter",
            post_id="tweet_123",
            source_event_id="evt_1",
            queue_item_id="qi_1",
            status="ok",
            duration_ms=1500.0,
            retry_count=2,
        )
        self.assertEqual(result.post_id, "tweet_123")
        self.assertEqual(result.duration_ms, 1500.0)
        self.assertEqual(result.retry_count, 2)

    def test_to_dict_roundtrip(self) -> None:
        result = PostResult(
            platform="instagram",
            post_id="ig_123",
            status="ok",
            metadata={"source": "test"},
        )
        data = result.to_dict()
        restored = PostResult.from_dict(data)
        self.assertEqual(restored.platform, "instagram")
        self.assertEqual(restored.post_id, "ig_123")
        self.assertEqual(restored.metadata, {"source": "test"})

    def test_error_status(self) -> None:
        result = PostResult(
            platform="facebook",
            status="failed",
            error="Rate limit exceeded",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Rate limit exceeded")


# ── ContentDedup Content Hash Tests ─────────────────────────


class TestContentDedupExtended(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.vault = SeenVault(self.backend)
        self.dedup = ContentDedup(self.vault)

    def tearDown(self) -> None:
        try:
            os.unlink(self.backend.path)
        except OSError:
            pass

    def test_content_hash_detects_duplicate_text(self) -> None:
        event = _make_event()
        text = "Duplicate response text"
        self.assertFalse(self.dedup.is_content_posted(event, PlatformType.FACEBOOK, text))
        self.dedup.mark_posted(event, PlatformType.FACEBOOK, text=text)
        self.assertTrue(self.dedup.is_content_posted(event, PlatformType.FACEBOOK, text))

    def test_content_hash_differs_for_different_text(self) -> None:
        event = _make_event()
        self.dedup.mark_posted(event, PlatformType.FACEBOOK, text="Hello")
        self.assertFalse(self.dedup.is_content_posted(event, PlatformType.FACEBOOK, "World"))

    def test_content_hash_differs_for_different_platform(self) -> None:
        event = _make_event()
        self.dedup.mark_posted(event, PlatformType.FACEBOOK, text="Same text")
        self.assertFalse(self.dedup.is_content_posted(event, PlatformType.TWITTER, "Same text"))


# ── ContentQueue Extended Tests ─────────────────────────────


class TestContentQueueExtended(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.dlq_backend = _make_tmp_backend()
        self.queue = ContentQueue(self.backend, dlq_backend=self.dlq_backend)

    def tearDown(self) -> None:
        for p in [self.backend.path, self.dlq_backend.path]:
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_enqueue_with_priority(self) -> None:
        low_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Low",
            priority=QUEUE_PRIORITY_LOW,
        )
        high_id = self.queue.enqueue(
            source_event_id="evt_2", platform="facebook", text="High",
            priority=QUEUE_PRIORITY_CRITICAL,
        )
        item = self.queue.dequeue()
        self.assertEqual(item.item_id, high_id)

    def test_enqueue_with_scheduling(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        item_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Later",
            scheduled_at=future,
        )
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_SCHEDULED)

    def test_scheduled_item_not_dequeued_early(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Later",
            scheduled_at=future,
        )
        item = self.queue.dequeue()
        self.assertIsNone(item)

    def test_scheduled_item_released(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        item_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Past",
            scheduled_at=past,
        )
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_PENDING)

    def test_ttl_expired_moves_to_failed(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Expire me",
            ttl_seconds=0,
        )
        self.queue.dequeue()
        time.sleep(0.05)
        self.queue._flush_expired()
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_FAILED)

    def test_dlq_stores_exhausted_items(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Fail me",
            max_retries=1,
        )
        self.queue.nack(item_id, error="fail", requeue=True)
        self.assertEqual(self.queue.dlq_count(), 1)
        dlq_items = self.queue.dlq_items()
        self.assertEqual(len(dlq_items), 1)
        self.assertEqual(dlq_items[0].item_id, item_id)

    def test_replay_from_dlq(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Replay me",
            max_retries=1,
        )
        self.queue.nack(item_id, error="fail", requeue=True)
        self.assertEqual(self.queue.dlq_count(), 1)

        self.queue.replay_dlq(item_id)
        self.assertEqual(self.queue.dlq_count(), 0)
        self.assertEqual(self.queue.pending_count(), 1)
        item = self.queue.dequeue()
        self.assertIsNotNone(item)
        self.assertEqual(item.retry_count, 0)

    def test_replay_all_dlq_replays_all(self) -> None:
        id1 = self.queue.enqueue(source_event_id="evt_d1", platform="facebook", text="DLQ 1", max_retries=0)
        id2 = self.queue.enqueue(source_event_id="evt_d2", platform="twitter", text="DLQ 2", max_retries=0)
        self.queue.nack(id1, error="e1", requeue=True)
        self.queue.nack(id2, error="e2", requeue=True)
        self.assertEqual(self.queue.dlq_count(), 2)

        replayed = self.queue.replay_all_dlq()
        self.assertEqual(replayed, 2)
        self.assertEqual(self.queue.dlq_count(), 0)
        self.assertEqual(self.queue.pending_count(), 2)

    def test_replay_all_dlq_empty(self) -> None:
        replayed = self.queue.replay_all_dlq()
        self.assertEqual(replayed, 0)

    def test_ack_after_nack_succeeds(self) -> None:
        item_id = self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="Retry then ok",
        )
        self.queue.dequeue()
        self.queue.nack(item_id, error="temp", requeue=True)
        self.queue.dequeue()
        self.queue.ack(item_id, post_id="post_ok")
        item = self.queue._items[item_id]
        self.assertEqual(item.status, QUEUE_STATUS_DONE)
        self.assertEqual(item.post_id, "post_ok")

    def test_scheduled_count(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="S1",
            scheduled_at=future,
        )
        self.queue.enqueue(
            source_event_id="evt_2", platform="facebook", text="S2",
            scheduled_at=future,
        )
        self.assertEqual(self.queue.scheduled_count(), 2)
        self.assertEqual(self.queue.scheduled_count(platform="facebook"), 2)

    def test_dlq_items_method(self) -> None:
        self.queue.enqueue(
            source_event_id="evt_1", platform="facebook", text="DLQ1",
            max_retries=0,
        )
        self.queue.dequeue()
        self.queue.nack(self.queue.all_items()[0].item_id, error="dlq", requeue=True)
        self.assertEqual(len(self.queue.dlq_items()), 1)

    def test_priority_ordering_mixed(self) -> None:
        ids = []
        for prio in [QUEUE_PRIORITY_LOW, QUEUE_PRIORITY_CRITICAL, QUEUE_PRIORITY_HIGH]:
            iid = self.queue.enqueue(
                source_event_id="evt", platform="facebook", text=f"P{prio}",
                priority=prio,
            )
            ids.append(iid)
        first = self.queue.dequeue()
        self.assertEqual(first.text, "P0")
        second = self.queue.dequeue()
        self.assertEqual(second.text, "P1")
        third = self.queue.dequeue()
        self.assertEqual(third.text, "P3")


# ── ContentGenerator Rule Engine Tests ──────────────────────


class TestContentGeneratorRules(TestCase):
    def test_rules_fire_when_template_returns_none(self) -> None:
        def rule_fn(event, platform):
            if event.event_type == EventType.LIKE:
                return "Rule response for like"
            return None

        generator = ContentGenerator(rule_fns=[rule_fn])
        event = mock.MagicMock(spec=BaseEvent)
        event.event_type = EventType.LIKE
        event.event_id = "evt_like"
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertIsNotNone(content)
        self.assertEqual(content.text, "Rule response for like")

    def test_rule_engine_fallback_to_template(self) -> None:
        def rule_fn(event, platform):
            return None

        generator = ContentGenerator(rule_fns=[rule_fn])
        event = _make_event(text="Normal message")
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertIsNotNone(content)
        self.assertIn("Thanks", content.text)

    def test_template_takes_priority_over_rules(self) -> None:
        def rule_fn(event, platform):
            return "Rule would say this"

        generator = ContentGenerator(rule_fns=[rule_fn])
        event = _make_event()
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertEqual(content.text, "Thanks for your message, Test User! We'll get back to you shortly.")

    def test_multiple_rules_tried_in_order_after_template(self) -> None:
        def rule1(event, platform):
            return None

        def rule2(event, platform):
            return "Rule 2 response"

        generator = ContentGenerator(rule_fns=[rule1, rule2])
        event = mock.MagicMock(spec=BaseEvent)
        event.event_type = EventType.LIKE
        event.event_id = "evt_like"
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertEqual(content.text, "Rule 2 response")

    def test_rule_exception_falls_through_to_next_rule(self) -> None:
        def failing_rule(event, platform):
            raise ValueError("Rule error")

        def fallback_rule(event, platform):
            return "Fallback rule response"

        generator = ContentGenerator(rule_fns=[failing_rule, fallback_rule])
        event = mock.MagicMock(spec=BaseEvent)
        event.event_type = EventType.LIKE
        event.event_id = "evt_like"
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertEqual(content.text, "Fallback rule response")

    def test_custom_generator_takes_priority(self) -> None:
        def custom(event, platform):
            return "Custom response"

        def rule(event, platform):
            return "Rule response"

        generator = ContentGenerator(generator_fn=custom, rule_fns=[rule])
        event = _make_event()
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertEqual(content.text, "Custom response")

    def test_rule_with_prompt_manager(self) -> None:
        pm = PromptManager()
        generator = ContentGenerator(prompt_manager=pm)
        event = _make_event(text="Test message")
        content = generator.generate(event, PlatformType.FACEBOOK)
        self.assertIsNotNone(content)
        self.assertIn("Thanks", content.text)

    def test_platform_char_limits(self) -> None:
        generator = ContentGenerator(platform_char_limits={
            PlatformType.TWITTER: 10,
        })
        event = _make_event()
        content = generator.generate(event, PlatformType.TWITTER)
        self.assertIsNotNone(content)
        self.assertTrue(len(content.text) <= 10)


# ── ContentDedup With Text Hash Integration ─────────────────


class TestContentDedupTextIntegration(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.vault = SeenVault(self.backend)
        self.dedup = ContentDedup(self.vault)

    def tearDown(self) -> None:
        try:
            os.unlink(self.backend.path)
        except OSError:
            pass

    def test_mark_posted_with_text_also_stores_content_hash(self) -> None:
        event = _make_event()
        text = "Some generated response"
        self.dedup.mark_posted(event, PlatformType.FACEBOOK, text=text)

        self.assertTrue(self.dedup.is_posted(event, PlatformType.FACEBOOK))
        self.assertTrue(self.dedup.is_content_posted(event, PlatformType.FACEBOOK, text))

    def test_event_dedup_independent_of_content_dedup(self) -> None:
        event = _make_event()
        self.dedup.mark_posted(event, PlatformType.FACEBOOK)
        self.assertTrue(self.dedup.is_posted(event, PlatformType.FACEBOOK))
        self.assertFalse(self.dedup.is_content_posted(event, PlatformType.FACEBOOK, "Any text"))

    def test_same_content_diff_event_still_deduped(self) -> None:
        event1 = _make_event(text="Hello")
        event2 = _make_event(text="World")
        text = "Same response"
        self.dedup.mark_posted(event1, PlatformType.FACEBOOK, text=text)
        self.assertTrue(self.dedup.is_content_posted(event2, PlatformType.FACEBOOK, text))


# ── ContentEngine DLQ Replay Tests ──────────────────────────


class TestContentEngineDlqReplay(TestCase):
    def setUp(self) -> None:
        self.backend = _make_tmp_backend()
        self.vault = SeenVault(self.backend)
        self.dedup = ContentDedup(self.vault)
        self.queue_backend = _make_tmp_backend()
        self.dlq_backend = _make_tmp_backend()
        self.queue = ContentQueue(self.queue_backend, dlq_backend=self.dlq_backend)
        self.generator = ContentGenerator()
        self.metrics = MetricsCollector()
        self.mock_pm = mock.MagicMock()
        self.mock_pm.send_event.return_value = {"status": "ok", "data": {"post_id": "p1"}}

        self.engine = ContentEngine(
            storage=mock.MagicMock(),
            event_bus=mock.MagicMock(),
            metrics=self.metrics,
            generator=self.generator,
            dedup=self.dedup,
            queue=self.queue,
            process_manager=self.mock_pm,
            dlq_replay_interval=2,
        )
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()
        for p in [self.backend.path, self.queue_backend.path, self.dlq_backend.path]:
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_replay_dlq_items_replays_all(self) -> None:
        id1 = self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="DLQ1", max_retries=0)
        id2 = self.queue.enqueue(source_event_id="evt_2", platform="twitter", text="DLQ2", max_retries=0)
        self.queue.nack(id1, error="e1", requeue=True)
        self.queue.nack(id2, error="e2", requeue=True)
        self.assertEqual(self.queue.dlq_count(), 2)

        self.engine._replay_dlq_items()

        self.assertEqual(self.queue.dlq_count(), 0)
        self.assertEqual(self.queue.pending_count(), 2)

    def test_replay_dlq_items_noop_when_empty(self) -> None:
        self.engine._replay_dlq_items()
        self.assertEqual(self.queue.pending_count(), 0)

    def test_dlq_replay_interval_triggers_replay(self) -> None:
        self.queue.enqueue(source_event_id="evt_1", platform="facebook", text="DLQ", max_retries=0)
        self.queue.nack(self.queue.all_items()[0].item_id, error="e", requeue=True)
        self.assertEqual(self.queue.dlq_count(), 1)
        self.assertEqual(self.queue.pending_count(), 0)

        self.engine._replay_dlq_items()

        self.assertEqual(self.queue.dlq_count(), 0)
        self.assertGreater(self.queue.pending_count(), 0)


# ── ContentGenerator OpenAI Integration Tests ────────────────


class TestContentGeneratorOpenAi(TestCase):
    def setUp(self) -> None:
        self.event = _make_event()

    def test_openai_used_when_api_key_set(self) -> None:
        with mock.patch.object(ContentGenerator, '_generate_openai', return_value="AI-generated response"):
            generator = ContentGenerator(openai_api_key="sk-test-key")
            content = generator.generate(self.event, PlatformType.FACEBOOK)

        self.assertIsNotNone(content)
        self.assertEqual(content.text, "AI-generated response")

    def test_openai_not_called_without_api_key(self) -> None:
        with mock.patch.object(ContentGenerator, '_generate_openai', return_value="AI-generated response"):
            generator = ContentGenerator()
            content = generator.generate(self.event, PlatformType.FACEBOOK)

        self.assertIsNotNone(content)
        self.assertIn("Thanks", content.text)

    def test_openai_failure_falls_through_to_template(self) -> None:
        with mock.patch.object(ContentGenerator, '_generate_openai', side_effect=RuntimeError("API error")):
            generator = ContentGenerator(openai_api_key="sk-test-key")
            content = generator.generate(self.event, PlatformType.FACEBOOK)

        self.assertIsNotNone(content)
        self.assertIn("Thanks", content.text)

    def test_openai_with_prompt_manager_format(self) -> None:
        prompt_mgr = PromptManager()
        prompt_mgr.register_template(PromptTemplate(
            event_type=EventType.MESSAGE,
            platform=PlatformType.FACEBOOK,
            template="Custom: {message_text}",
        ))

        with mock.patch.object(ContentGenerator, '_generate_openai', return_value="AI formatted response"):
            generator = ContentGenerator(
                openai_api_key="sk-test-key",
                prompt_manager=prompt_mgr,
            )
            content = generator.generate(self.event, PlatformType.FACEBOOK)

        self.assertIsNotNone(content)
        self.assertEqual(content.text, "AI formatted response")


