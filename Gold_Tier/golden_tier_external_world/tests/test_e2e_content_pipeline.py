from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest import TestCase, mock
import os
import shutil
import tempfile
import time

from golden_tier_external_world.config.enums import ContentCategory, EventType, PlatformType
from golden_tier_external_world.events.models import MessageEvent
from golden_tier_external_world.events.bus import ProductionEventBus
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.storage.file_storage import FileStorage
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.planner import Planner
from golden_tier_external_world.content_orchestrator.generator import ContentGenerator
from golden_tier_external_world.content_orchestrator.dedup import ContentDedup
from golden_tier_external_world.content_orchestrator.queue import ContentQueue
from golden_tier_external_world.content_orchestrator.engine import ContentEngine
from golden_tier_external_world.content_orchestrator.validator import ContentValidator
from golden_tier_external_world.content_orchestrator.rate_limiter import RateLimiter


class TestEndToEndContentPipeline(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

        self.storage = FileStorage(self.tmp / "data")
        self.metrics = MetricsCollector()
        self.bus = ProductionEventBus(max_workers=2)

        queue_backend = JsonBackend(self.tmp / "content_queue.json")
        dlq_backend = JsonBackend(self.tmp / "content_dlq.json")
        self.queue = ContentQueue(backend=queue_backend, dlq_backend=dlq_backend)

        dedup_backend = JsonBackend(self.tmp / "content_dedup.json")
        dedup_vault = SeenVault(dedup_backend)
        self.dedup = ContentDedup(dedup_vault)

        self.generator = ContentGenerator()
        self.validator = ContentValidator()
        self.rate_limiter = RateLimiter()

        self.mock_pm = mock.MagicMock()
        self.mock_pm.send_event.return_value = {
            "status": "ok",
            "data": {"post_id": "post_e2e_abc"},
        }

        self.engine = ContentEngine(
            storage=self.storage,
            event_bus=self.bus,
            metrics=self.metrics,
            generator=self.generator,
            dedup=self.dedup,
            queue=self.queue,
            process_manager=self.mock_pm,
            validator=self.validator,
            rate_limiter=self.rate_limiter,
        )

        self.planner = Planner(
            storage=self.storage,
            event_bus=self.bus,
            metrics=self.metrics,
            content_engine=self.engine,
        )

        self.engine.set_planner_callback(self.planner.content_callback)

    def tearDown(self) -> None:
        self.engine.stop()
        self.planner.shutdown()
        self.bus.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_message_event(
        self,
        event_id: str = "e2e_msg_1",
        text: str = "Hello from integration test!",
        platform: PlatformType = PlatformType.FACEBOOK,
    ) -> MessageEvent:
        sender = PlatformAccount(
            platform=platform,
            account_id="u_e2e",
            display_name="E2E User",
            username="e2euser",
        )
        content = ContentItem(
            content_id=f"ci_{event_id}",
            platform=platform,
            content_type=ContentCategory.TEXT,
            text=text,
        )
        return MessageEvent(
            event_id=event_id,
            platform=platform,
            timestamp=datetime.now(timezone.utc),
            sender=sender,
            content=content,
            conversation_id="conv_e2e",
        )

    def test_full_pipeline_event_to_published(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        event = self._make_message_event()
        corr_id = self.bus.publish(event)
        self.assertIsNotNone(corr_id)

        time.sleep(0.5)

        snap = self.metrics.snapshot()
        self.assertGreaterEqual(snap["total_handled"], 1)
        self.assertGreaterEqual(snap["total_published"], 1)

        self.assertEqual(self.queue.pending_count(), 3)

        for _ in range(3):
            self.engine._process_one_item()

        self.assertEqual(self.queue.pending_count(), 0)
        self.assertEqual(self.mock_pm.send_event.call_count, 3)

        post_snap = self.metrics.post_snapshot()
        ok_keys = [k for k in post_snap if k.endswith(":ok")]
        self.assertEqual(len(ok_keys), 3)

    def test_dedup_prevents_duplicate_across_pipeline(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        event = self._make_message_event()

        self.bus.publish(event)
        time.sleep(0.3)

        self.bus.publish(event)
        time.sleep(0.3)

        snap = self.metrics.snapshot()
        self.assertEqual(snap["total_handled"], 2)

        self.assertLessEqual(self.queue.pending_count(), 3)

    def test_ignored_event_type_does_not_generate_content(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        sender = PlatformAccount(
            platform=PlatformType.FACEBOOK,
            account_id="u_ignore",
            display_name="Ignored",
            username="ignored",
        )
        content = ContentItem(
            content_id="ci_like",
            platform=PlatformType.FACEBOOK,
            content_type=ContentCategory.TEXT,
            text="Nice post!",
        )
        from golden_tier_external_world.events.models import LikeEvent

        event = LikeEvent(
            event_id="e2e_like_1",
            platform=PlatformType.FACEBOOK,
            timestamp=datetime.now(timezone.utc),
            actor=sender,
            target_content_id="target_content_1",
        )

        self.bus.publish(event)
        time.sleep(0.3)

        self.assertEqual(self.queue.pending_count(), 0)
        self.mock_pm.send_event.assert_not_called()

    def test_planner_content_callback_receives_lifecycle(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        callback_log: list[str] = []

        def logged_callback(phase: str, data: dict[str, Any]) -> None:
            callback_log.append(phase)

        self.engine.set_planner_callback(logged_callback)

        event = self._make_message_event()
        self.bus.publish(event)
        time.sleep(0.3)

        self.assertIn("generation_started", callback_log)
        self.assertIn("generation_finished", callback_log)

        self.engine._process_one_item()

        self.assertIn("posting_started", callback_log)

    def test_rate_limiter_stops_excess_posts(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        tight_limiter = RateLimiter(limits={PlatformType.FACEBOOK: [0, 86400]})

        self.engine._rate_limiter = tight_limiter

        event = self._make_message_event()
        self.bus.publish(event)
        time.sleep(0.3)

        for _ in range(5):
            self.engine._process_one_item()

        self.assertEqual(self.mock_pm.send_event.call_count, 0)

    def test_validator_rejects_invalid_content(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        strict_validator = ContentValidator()
        strict_validator._max_lengths[PlatformType.FACEBOOK] = 10

        self.engine._validator = strict_validator
        self.engine._target_platforms = [PlatformType.FACEBOOK]

        event = self._make_message_event(text="This is way too long for the validator to accept")
        self.bus.publish(event)
        time.sleep(0.3)

        self.assertEqual(self.queue.pending_count(), 0)
        self.mock_pm.send_event.assert_not_called()

    def test_pipeline_with_content_hash_dedup(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        event1 = self._make_message_event(event_id="e2e_dedup_1", text="Same text")
        event2 = self._make_message_event(event_id="e2e_dedup_2", text="Same text")

        self.bus.publish(event1)
        time.sleep(0.3)

        self.bus.publish(event2)
        time.sleep(0.3)

        items = self.queue.all_items()
        texts = [i.text for i in items]
        unique_texts = set(texts)

        self.assertEqual(len(items), 3)
        self.assertEqual(len(unique_texts), 1)

    def test_metrics_snapshot_includes_content_stats(self) -> None:
        self.bus.start()
        self.engine.start()
        self.planner.register()

        event = self._make_message_event()
        self.bus.publish(event)
        time.sleep(0.3)

        self.engine._process_one_item()

        snap = self.metrics.snapshot()
        self.assertIn("by_event_type", snap)
