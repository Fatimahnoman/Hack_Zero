import tempfile
import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from golden_tier_external_world.posters import BasePoster, PostContent, PosterError
from golden_tier_external_world.posters.twitter import TwitterPoster
from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.events.bus import EventBus


def _mock_page() -> MagicMock:
    page = MagicMock()
    page.url = "https://x.com/home"
    page.query_selector = MagicMock(return_value=None)
    page.query_selector_all = MagicMock(return_value=[])
    page.goto = MagicMock()
    page.fill = MagicMock()
    page.click = MagicMock()
    page.close = MagicMock()
    page.set_default_timeout = MagicMock()
    return page


def _make_poster(
    storage: StorageInterface,
    bus: EventBus,
    browser_manager: MagicMock = None,
    **kwargs: object,
) -> TwitterPoster:
    opts: dict = {
        "storage": storage,
        "event_bus": bus,
        "max_retries": 2,
        "backoff_base": 0.01,
        "backoff_max": 0.1,
        "backoff_jitter": 0.0,
    }
    if browser_manager is not None:
        opts["browser_manager"] = browser_manager
    opts.update(kwargs)
    return TwitterPoster(**opts)


class TestTwitterPoster(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.storage = MagicMock(spec=StorageInterface)
        self.bus = MagicMock(spec=EventBus)
        self.bm = MagicMock()
        self.bm.is_running = False
        self.bm.screenshot = MagicMock()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_platform_is_twitter(self) -> None:
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        self.assertEqual(poster.platform, PlatformType.TWITTER)

    def test_authenticate_already_logged_in(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if "SideNav" in sel or "Post" in sel or "Profile" in sel or "primaryColumn" in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        result = poster.authenticate()

        self.assertTrue(result)
        self.assertTrue(poster._authenticated)
        page.goto.assert_called_once_with("https://x.com", timeout=30000)
        page.close.assert_called_once()

    def test_authenticate_not_logged_in_no_creds(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        self.bm.new_page.return_value = page

        poster = _make_poster(
            self.storage, self.bus, browser_manager=self.bm,
            twitter_username=None, twitter_password=None,
        )
        result = poster.authenticate()

        self.assertFalse(result)
        self.assertFalse(poster._authenticated)

    def test_authenticate_performs_login(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if "text" in sel and "name" in sel and "input" in sel:
                return MagicMock()
            if "password" in sel:
                return MagicMock()
            if "Log in" in sel or "Next" in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        self.bm.new_page.return_value = page

        def is_logged_in_side_effect(p: MagicMock) -> bool:
            if hasattr(is_logged_in_side_effect, "call_count"):
                is_logged_in_side_effect.call_count += 1
            else:
                is_logged_in_side_effect.call_count = 1
            return is_logged_in_side_effect.call_count > 1

        original = TwitterPoster._is_logged_in
        with patch.object(
            TwitterPoster, "_is_logged_in",
            side_effect=lambda p: is_logged_in_side_effect(p),
        ):
            poster = _make_poster(
                self.storage, self.bus, browser_manager=self.bm,
                twitter_username="testuser", twitter_password="secret",
            )
            result = poster.authenticate()

        self.assertTrue(result)
        self.assertTrue(poster._authenticated)

    def test_post_success(self) -> None:
        page = _mock_page()
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = True

        with patch.object(TwitterPoster, "_open_post_composer", return_value=None):
            with patch.object(TwitterPoster, "_enter_post_text", return_value=None):
                with patch.object(TwitterPoster, "_publish", return_value="post_123"):
                    content = PostContent(text="Hello Twitter!")
                    post_id = poster.post(content)

        self.assertEqual(post_id, "post_123")

    def test_post_media_upload(self) -> None:
        page = _mock_page()
        self.bm.new_page.return_value = page
        file_input = MagicMock()
        file_input.set_input_files = MagicMock()

        def qs(sel: str) -> MagicMock:
            if 'input[type="file"]' in sel:
                return file_input
            return None

        page.query_selector = qs

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = True

        media_file = self.tmp_dir / "test.jpg"
        media_file.write_text("fake-image-data")

        with patch.object(TwitterPoster, "_open_post_composer", return_value=None):
            with patch.object(TwitterPoster, "_enter_post_text", return_value=None):
                with patch.object(TwitterPoster, "_publish", return_value="tweet_456"):
                    content = PostContent(
                        text="With media",
                        media_paths=[str(media_file)],
                    )
                    post_id = poster.post(content)

        self.assertEqual(post_id, "tweet_456")
        file_input.set_input_files.assert_called_once_with([str(media_file)])

    def test_post_raises_when_not_authenticated(self) -> None:
        page = _mock_page()
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = False

        with self.assertRaises(PosterError):
            poster.post(PostContent(text="test"))

    def test_is_logged_in_detects_login_page(self) -> None:
        page = _mock_page()
        page.url = "https://x.com/i/flow/login"
        result = TwitterPoster._is_logged_in(page)
        self.assertFalse(result)

    def test_is_logged_in_detects_home(self) -> None:
        page = _mock_page()
        page.url = "https://x.com/home"
        page.query_selector = MagicMock(return_value=MagicMock())
        result = TwitterPoster._is_logged_in(page)
        self.assertTrue(result)

    def test_publish_extracts_tweet_id(self) -> None:
        page = _mock_page()
        page.url = "https://x.com/testuser/status/1234567890123456789"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertEqual(post_id, "1234567890123456789")

    def test_publish_fallback_when_no_id_found(self) -> None:
        page = _mock_page()
        page.url = "https://x.com/home"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertTrue(post_id.startswith("tw_post_"))

    def test_publish_button_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        with self.assertRaises(PosterError):
            poster._publish(page)

    def test_composer_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            TwitterPoster._open_post_composer(page)

    def test_text_area_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            TwitterPoster._enter_post_text(page, "test")

    def test_dismiss_popups_handles_no_popups(self) -> None:
        page = _mock_page()
        page.query_selector_all.return_value = []
        TwitterPoster._dismiss_popups(page)

    def test_fill_login_step_username(self) -> None:
        page = _mock_page()
        username_field = MagicMock()
        username_field.fill = MagicMock()

        def qs(sel: str) -> MagicMock:
            if 'autocomplete="username"' in sel:
                return username_field
            return None

        page.query_selector = qs
        TwitterPoster._fill_login_step(page, "username", "testuser")
        username_field.fill.assert_called_once_with("testuser")

    def test_fill_login_step_password(self) -> None:
        page = _mock_page()
        password_field = MagicMock()
        password_field.fill = MagicMock()

        def qs(sel: str) -> MagicMock:
            if 'type="password"' in sel:
                return password_field
            return None

        page.query_selector = qs
        TwitterPoster._fill_login_step(page, "password", "secret123")
        password_field.fill.assert_called_once_with("secret123")

    def test_fill_login_step_field_not_found(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            TwitterPoster._fill_login_step(page, "username", "test")

    def test_login_handles_unusual_activity(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if 'name="text"' in sel:
                return MagicMock()
            if "password" in sel:
                return MagicMock()
            if "Log in" in sel or "Next" in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        self.bm.new_page.return_value = page

        with patch.object(TwitterPoster, "_is_logged_in", return_value=True):
            poster = _make_poster(
                self.storage, self.bus, browser_manager=self.bm,
                twitter_username="testuser", twitter_password="secret",
                twitter_email="email@test.com",
            )
            poster._login(page)

        self.bm.screenshot.assert_not_called()
