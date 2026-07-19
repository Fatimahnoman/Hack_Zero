from unittest import TestCase, mock
from datetime import datetime
from pathlib import Path
import tempfile

from golden_tier_external_world.config.enums import PlatformType, WatcherState
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.watchers.instagram import InstagramWatcher
from golden_tier_external_world.events.models import (
    BaseEvent,
    MessageEvent,
    CommentEvent,
    MentionEvent,
    LikeEvent,
    FollowEvent,
)
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.events.bus import EventBus, LocalEventBus


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

    def get_last_poll_time(self, platform: PlatformType) -> str | None:
        return self._poll_times.get(platform.value if isinstance(platform, PlatformType) else platform)

    def set_last_poll_time(self, platform: PlatformType, timestamp: str) -> None:
        self._poll_times[platform.value if isinstance(platform, PlatformType) else platform] = timestamp

    def save_state(self, platform: PlatformType, key: str, value: object) -> None:
        self._states[f"{platform.value}/{key}"] = value

    def load_state(self, platform: PlatformType, key: str) -> object | None:
        return self._states.get(f"{platform.value}/{key}")


def make_config() -> WatcherConfig:
    return WatcherConfig(
        platform=PlatformType.INSTAGRAM,
        poll_interval_seconds=10,
        max_events_per_poll=20,
        enabled=True,
    )


class TestInstagramWatcher(TestCase):
    def setUp(self) -> None:
        self.config = make_config()
        self.storage = DummyStorage()
        self.bus: EventBus = LocalEventBus()
        self._tmp_seen = Path(tempfile.mktemp(suffix=".json"))
        seen = SeenVault(JsonBackend(self._tmp_seen, auto_create=True))
        self.watcher = InstagramWatcher(
            self.config, self.storage, self.bus, seen_vault=seen,
        )
        self.watcher._authenticated = True

    def tearDown(self) -> None:
        if self._tmp_seen.exists():
            self._tmp_seen.unlink()

    def test_authenticate_returns_true(self) -> None:
        mock_browser = mock.MagicMock()
        mock_page = mock.MagicMock()
        mock_page.url = "https://www.instagram.com/"
        mock_el = mock.MagicMock()
        mock_el.is_visible.return_value = True
        mock_page.query_selector.return_value = mock_el
        mock_browser.new_page.return_value = mock_page
        self.watcher._browser = mock_browser
        result = self.watcher.authenticate()
        self.assertTrue(result)
        self.assertTrue(self.watcher._authenticated)

    def test_default_poll_returns_empty(self) -> None:
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=[]):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=[]):
                events = self.watcher.poll()
        self.assertEqual(events, [])

    def test_scrape_inbox_creates_message_events(self) -> None:
        mock_data = [
            {
                "thread_id": "thread_1",
                "item_id": "item_1",
                "sender_id": "user_123",
                "sender_username": "test_user",
                "text": "Hello!",
                "timestamp": 1700000000,
                "has_image": False,
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=mock_data):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=[]):
                events = self.watcher.poll()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, MessageEvent)
        self.assertTrue(event.event_id.startswith("ig_dm_thread_1_"))
        self.assertEqual(event.sender.username, "test_user")
        self.assertEqual(event.content.text, "Hello!")

    def test_scrape_inbox_with_image(self) -> None:
        mock_data = [
            {
                "thread_id": "t1",
                "item_id": "i1",
                "sender_id": "u1",
                "sender_username": "photo_guy",
                "text": "Check this",
                "timestamp": 1700000000,
                "has_image": True,
                "media_urls": ["https://instagram.com/p/abc123"],
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=mock_data):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=[]):
                events = self.watcher.poll()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, MessageEvent)
        self.assertEqual(event.content.text, "Check this")
        self.assertEqual(event.conversation_id, "t1")

    def test_scrape_notifications_comment(self) -> None:
        mock_data = [
            {
                "id": "n1",
                "actor_id": "u2",
                "actor_username": "commenter",
                "type": "comment",
                "text": "Nice pic!",
                "media_id": "m1",
                "media_code": "abc123",
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=[]):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=mock_data):
                events = self.watcher.poll()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, CommentEvent)
        self.assertEqual(event.author.username, "commenter")

    def test_scrape_notifications_mention(self) -> None:
        mock_data = [
            {
                "id": "n2",
                "actor_id": "u3",
                "actor_username": "mentions_user",
                "type": "mention",
                "text": "@you check this",
                "media_id": "m2",
                "media_code": "def456",
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=[]):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=mock_data):
                events = self.watcher.poll()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, MentionEvent)
        self.assertEqual(
            event.source_url,
            "https://instagram.com/p/def456",
        )

    def test_scrape_notifications_like(self) -> None:
        mock_data = [
            {
                "id": "n3",
                "actor_id": "u4",
                "actor_username": "liker",
                "type": "like",
                "media_id": "m3",
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=[]):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=mock_data):
                events = self.watcher.poll()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, LikeEvent)
        self.assertEqual(event.actor.username, "liker")

    def test_scrape_notifications_follow(self) -> None:
        mock_data = [
            {
                "id": "n4",
                "actor_id": "u5",
                "actor_username": "new_follower",
                "type": "follow",
                "target_id": "my_id",
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=[]):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=mock_data):
                events = self.watcher.poll()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, FollowEvent)
        self.assertEqual(event.follower.username, "new_follower")

    def test_scrape_notifications_unknown_type_skipped(self) -> None:
        mock_data = [
            {
                "id": "n5",
                "actor_id": "u6",
                "actor_username": "someone",
                "type": "unknown_event",
            },
        ]
        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=[]):
            with mock.patch.object(self.watcher, "_scrape_notifications", return_value=mock_data):
                events = self.watcher.poll()

        self.assertEqual(len(events), 0)

    def test_dedup_inbox_items(self) -> None:
        mock_data = [
            {
                "thread_id": "t1",
                "item_id": "i1",
                "sender_id": "u1",
                "sender_username": "user1",
                "text": "First",
                "timestamp": 1700000000,
                "has_image": False,
            },
        ]
        event_id = f"ig_dm_t1_{hash('First') & 0xFFFFFFFF}"
        self.watcher._seen.mark_seen(event_id)

        with mock.patch.object(self.watcher, "_scrape_inbox", return_value=mock_data):
            events = self.watcher.poll()

        self.assertEqual(len(events), 0)

    def test_poll_exception_sets_error_state(self) -> None:
        with mock.patch.object(self.watcher, "_scrape_inbox", side_effect=Exception("network error")):
            events = self.watcher.poll()

        self.assertEqual(events, [])
        self.assertEqual(self.watcher.state, WatcherState.ERROR)
