from unittest import TestCase
from pathlib import Path
import json
import tempfile
import shutil
import time

from golden_tier_external_world.config.enums import PlatformType, EventType, ContentCategory
from golden_tier_external_world.events.models import MessageEvent, ProfileViewEvent
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.events.bus import ProductionEventBus
from golden_tier_external_world.storage.file_storage import FileStorage
from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.planner import Planner
from golden_tier_external_world.storage.interface import StorageInterface
from datetime import datetime, timezone


class TestPlannerIntegration(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.storage = FileStorage(self.tmp / "data")
        self.metrics = MetricsCollector()
        self.bus = ProductionEventBus(max_workers=1)
        self.planner = Planner(
            storage=self.storage,
            event_bus=self.bus,
            metrics=self.metrics,
        )

    def tearDown(self) -> None:
        self.planner.shutdown()
        self.bus.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_msg_event(self) -> MessageEvent:
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
            text="Hello from LinkedIn",
        )
        return MessageEvent(
            event_id="test_msg_1",
            platform=PlatformType.LINKEDIN,
            timestamp=datetime.now(timezone.utc),
            sender=sender,
            content=content,
            conversation_id="conv1",
        )

    def test_planner_receives_event(self) -> None:
        self.bus.start()
        self.planner.register()

        event = self._make_msg_event()
        corr_id = self.bus.publish(event)
        self.assertIsNotNone(corr_id)

        time.sleep(0.3)

        snap = self.metrics.snapshot()
        self.assertEqual(snap["total_published"], 1)
        self.assertEqual(snap["total_handled"], 1)
        self.assertEqual(snap["by_event_type"].get("MESSAGE"), 1)

    def test_planner_receives_multiple_event_types(self) -> None:
        self.bus.start()
        self.planner.register()

        msg = self._make_msg_event()
        self.bus.publish(msg)

        viewer = PlatformAccount(
            platform=PlatformType.LINKEDIN,
            account_id="u2",
            display_name="Bob",
            username="bob",
        )
        pv = ProfileViewEvent(
            event_id="test_pv_1",
            platform=PlatformType.LINKEDIN,
            timestamp=datetime.now(timezone.utc),
            viewer=viewer,
            headline="Engineer",
        )
        self.bus.publish(pv)

        time.sleep(0.3)

        snap = self.metrics.snapshot()
        self.assertEqual(snap["total_published"], 2)
        self.assertEqual(snap["total_handled"], 2)
        self.assertEqual(snap["by_event_type"].get("MESSAGE"), 1)
        self.assertEqual(snap["by_event_type"].get("PROFILE_VIEW"), 1)

    def test_storage_persists_event(self) -> None:
        event = self._make_msg_event()
        self.storage.save_event(event)
        self.storage.mark_processed(event.event_id)
        self.assertTrue(self.storage.is_processed(event.event_id))

        event_file = self.tmp / "data" / "events" / "linkedin" / "message.jsonl"
        self.assertTrue(event_file.exists())
        lines = event_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["id"], "test_msg_1")

    def test_storage_state_persistence(self) -> None:
        self.storage.save_state(PlatformType.LINKEDIN, "poll_count", 5)
        self.storage.set_last_poll_time(
            PlatformType.LINKEDIN, "2026-01-01T00:00:00",
        )

        result = self.storage.load_state(PlatformType.LINKEDIN, "poll_count")
        self.assertEqual(result, 5)

        poll_time = self.storage.get_last_poll_time(PlatformType.LINKEDIN)
        self.assertEqual(poll_time, "2026-01-01T00:00:00")
