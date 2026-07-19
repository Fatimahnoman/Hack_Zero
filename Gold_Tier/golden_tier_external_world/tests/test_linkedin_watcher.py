from unittest import TestCase, mock
from pathlib import Path
from datetime import datetime, timezone
import tempfile

from golden_tier_external_world.config.enums import PlatformType, WatcherState
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.watchers.linkedin import LinkedInWatcher
from golden_tier_external_world.events.models import (
    BaseEvent,
    MessageEvent,
    ConnectionRequestEvent,
    MentionEvent,
    CommentEvent,
    ProfileViewEvent,
    NotificationEvent,
)
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.interface import StorageInterface
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
        platform=PlatformType.LINKEDIN,
        poll_interval_seconds=10,
    )


class TestLinkedInWatcher(TestCase):
    def setUp(self) -> None:
        self.storage = DummyStorage()
        self.bus: EventBus = LocalEventBus()
        self.tmp_dir = Path(tempfile.mkdtemp())
        seen_backend = JsonBackend(self.tmp_dir / "test_seen.json")
        self.seen = SeenVault(seen_backend)
        self.config = make_config()
        self.watcher = LinkedInWatcher(
            config=self.config,
            storage=self.storage,
            event_bus=self.bus,
            seen_vault=self.seen,
        )

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_poll_returns_empty(self) -> None:
        events = self.watcher.poll()
        self.assertEqual(events, [])

    def test_authenticate_returns_false(self) -> None:
        result = self.watcher.authenticate()
        self.assertFalse(result)

    def test_poll_when_unauthenticated_sets_error_state(self) -> None:
        self.watcher._session_valid = False
        events = self.watcher.poll()
        self.assertEqual(events, [])
        self.assertEqual(self.watcher.state, WatcherState.ERROR)

    def test_scrape_messages_duplicate_dedup(self) -> None:
        raw = [
            {
                "id": "m1",
                "conversation_id": "c1",
                "text": "Hello",
                "sender_id": "u1",
                "sender_name": "Alice",
                "sender_urn": "urn:li:alice",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "conversation_url": "https://linkedin.com/in/mail/c1",
            },
        ]
        self.watcher._scrape_messages = mock.Mock(return_value=raw)
        self.watcher._session_valid = True

        scraped = self.watcher._scrape_messages()
        self.assertEqual(scraped, raw)

        dedup_id = f"linkedin_msg_{raw[0]['conversation_id']}_{raw[0]['id']}"
        seen_before = self.watcher.seen_vault.is_seen(dedup_id)
        count_before = self.watcher.seen_vault.count()

        events1 = self.watcher._fetch_messages()
        self.assertEqual(len(events1), 1)

        events2 = self.watcher._fetch_messages()
        self.assertEqual(len(events2), 0)

        self.assertIsInstance(events1[0], MessageEvent)
        self.assertEqual(events1[0].conversation_id, "c1")

    def test_scrape_connection_requests_dedup(self) -> None:
        raw = [
            {
                "invitation_id": "inv1",
                "sender_id": "u2",
                "sender_name": "Bob",
                "sender_urn": "urn:li:bob",
                "message": "Let's connect!",
            },
        ]
        self.watcher._scrape_connection_requests = mock.Mock(return_value=raw)

        events1 = self.watcher._fetch_connection_requests()
        self.assertEqual(len(events1), 1)
        self.assertIsInstance(events1[0], ConnectionRequestEvent)
        self.assertEqual(events1[0].sender.display_name, "Bob")
        self.assertEqual(events1[0].message, "Let's connect!")

        events2 = self.watcher._fetch_connection_requests()
        self.assertEqual(len(events2), 0)

    def test_scrape_notifications_mention(self) -> None:
        raw = [
            {
                "id": "n1",
                "type": "post_mention",
                "actor_id": "u3",
                "actor_name": "Charlie",
                "actor_urn": "urn:li:charlie",
                "text": "Great post!",
                "post_id": "p1",
                "post_url": "https://linkedin.com/feed/p1",
                "timestamp": "2026-02-01T00:00:00+00:00",
            },
        ]
        self.watcher._scrape_notifications = mock.Mock(return_value=raw)

        events = self.watcher._fetch_notifications()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], MentionEvent)
        self.assertEqual(events[0].mentioned_by.display_name, "Charlie")

    def test_scrape_notifications_comment(self) -> None:
        raw = [
            {
                "id": "n2",
                "type": "comment_on_post",
                "actor_id": "u4",
                "actor_name": "Diana",
                "actor_urn": "urn:li:diana",
                "text": "Nice work!",
                "post_id": "p2",
                "post_url": "https://linkedin.com/feed/p2",
                "timestamp": "2026-03-01T00:00:00+00:00",
            },
        ]
        self.watcher._scrape_notifications = mock.Mock(return_value=raw)

        events = self.watcher._fetch_notifications()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], CommentEvent)
        self.assertEqual(events[0].author.display_name, "Diana")

    def test_scrape_notifications_profile_view(self) -> None:
        raw = [
            {
                "id": "n3",
                "type": "profile_view",
                "actor_id": "u5",
                "actor_name": "Eve",
                "actor_urn": "urn:li:eve",
                "viewer_headline": "Engineer at Corp",
                "timestamp": "2026-04-01T00:00:00+00:00",
            },
        ]
        self.watcher._scrape_notifications = mock.Mock(return_value=raw)

        events = self.watcher._fetch_notifications()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], ProfileViewEvent)
        self.assertEqual(events[0].viewer.display_name, "Eve")
        self.assertEqual(events[0].headline, "Engineer at Corp")

    def test_scrape_notifications_like(self) -> None:
        raw = [
            {
                "id": "n4",
                "type": "like",
                "actor_id": "u6",
                "actor_name": "Frank",
                "actor_urn": "urn:li:frank",
                "text": "liked your post",
                "post_id": "p3",
                "post_url": "https://linkedin.com/feed/p3",
            },
        ]
        self.watcher._scrape_notifications = mock.Mock(return_value=raw)

        events = self.watcher._fetch_notifications()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], NotificationEvent)
        self.assertEqual(events[0].notification_type, "like")
        self.assertEqual(events[0].actor.display_name, "Frank")

    def test_scrape_notifications_unknown_type(self) -> None:
        raw = [
            {
                "id": "n5",
                "type": "some_new_type",
                "actor_id": "u7",
                "actor_name": "Grace",
                "actor_urn": "urn:li:grace",
                "text": "did something new",
            },
        ]
        self.watcher._scrape_notifications = mock.Mock(return_value=raw)

        events = self.watcher._fetch_notifications()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], NotificationEvent)
        self.assertEqual(events[0].notification_type, "some_new_type")

    def test_seen_vault_property(self) -> None:
        self.assertIsNotNone(self.watcher.seen_vault)
        self.assertIsInstance(self.watcher.seen_vault, SeenVault)

    def test_full_poll_cycle(self) -> None:
        self.watcher._session_valid = True
        self.watcher._scrape_messages = mock.Mock(return_value=[
            {
                "id": "m2",
                "conversation_id": "c2",
                "text": "Hi",
                "sender_id": "u1",
                "sender_name": "Alice",
                "sender_urn": "urn:li:alice",
                "timestamp": "2026-05-01T00:00:00+00:00",
            },
        ])
        self.watcher._scrape_connection_requests = mock.Mock(return_value=[
            {
                "invitation_id": "inv2",
                "sender_id": "u2",
                "sender_name": "Bob",
                "sender_urn": "urn:li:bob",
            },
        ])
        self.watcher._scrape_notifications = mock.Mock(return_value=[
            {
                "id": "n6",
                "type": "post_mention",
                "actor_id": "u3",
                "actor_name": "Charlie",
                "actor_urn": "urn:li:charlie",
                "text": "Nice!",
                "post_id": "p4",
                "timestamp": "2026-06-01T00:00:00+00:00",
            },
        ])

        events = self.watcher.poll()
        self.assertEqual(len(events), 3)

        types = {type(e).__name__ for e in events}
        self.assertEqual(types, {"MessageEvent", "ConnectionRequestEvent", "MentionEvent"})

    def test_poll_exception_sets_error_state(self) -> None:
        self.watcher._session_valid = True
        self.watcher._scrape_messages = mock.Mock(side_effect=RuntimeError("boom"))

        events = self.watcher.poll()
        self.assertEqual(events, [])
        self.assertEqual(self.watcher.state, WatcherState.ERROR)

    def test_vault_path_creates_seen_vault(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault_dir = Path(td)
            w = LinkedInWatcher(
                config=self.config,
                storage=self.storage,
                event_bus=self.bus,
                vault_path=vault_dir,
            )
            self.assertIsNotNone(w.seen_vault)
            w.seen_vault.mark_seen("test_dummy")
            self.assertTrue((vault_dir / "linkedin_seen.json").exists())
