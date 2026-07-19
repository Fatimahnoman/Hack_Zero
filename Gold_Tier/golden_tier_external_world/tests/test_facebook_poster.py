import tempfile
import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch, PropertyMock

from golden_tier_external_world.posters import BasePoster, PostContent, PosterError
from golden_tier_external_world.posters.facebook import FacebookPoster
from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.events.bus import EventBus, LocalEventBus


def _mock_page() -> MagicMock:
    page = MagicMock()
    page.url = "https://www.facebook.com/"
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
) -> FacebookPoster:
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
    return FacebookPoster(**opts)


class TestPostContent(TestCase):
    def test_text_only(self) -> None:
        c = PostContent(text="Hello world")
        self.assertEqual(c.text, "Hello world")
        self.assertEqual(c.media_paths, [])
        self.assertEqual(c.visibility, "public")

    def test_media_paths_converted(self) -> None:
        c = PostContent(text="Post", media_paths=["a.jpg", "b.png"])
        self.assertEqual(len(c.media_paths), 2)
        self.assertIsInstance(c.media_paths[0], Path)
        self.assertEqual(c.media_paths[0].name, "a.jpg")


class TestBasePoster(TestCase):
    def test_abstract_cannot_instantiate(self) -> None:
        with self.assertRaises(TypeError):
            BasePoster(storage=MagicMock(), event_bus=MagicMock())  # type: ignore[abstract]

    def test_retry_succeeds_on_first_attempt(self) -> None:
        storage = MagicMock(spec=StorageInterface)
        bus = MagicMock(spec=EventBus)
        bm = MagicMock()
        bm.is_running = False

        poster = _make_poster(storage, bus, browser_manager=bm, max_retries=3)
        fn = MagicMock(return_value="ok")
        result = poster.retry(fn)
        self.assertEqual(result, "ok")
        fn.assert_called_once()

    def test_retry_succeeds_on_third_attempt(self) -> None:
        storage = MagicMock(spec=StorageInterface)
        bus = MagicMock(spec=EventBus)
        bm = MagicMock()
        bm.is_running = False

        poster = _make_poster(storage, bus, browser_manager=bm, max_retries=3)
        fn = MagicMock(side_effect=[ValueError("fail1"), ValueError("fail2"), "ok"])
        result = poster.retry(fn)
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 3)

    def test_retry_exhausted_raises(self) -> None:
        storage = MagicMock(spec=StorageInterface)
        bus = MagicMock(spec=EventBus)
        bm = MagicMock()
        bm.is_running = False

        poster = _make_poster(storage, bus, browser_manager=bm, max_retries=2)
        fn = MagicMock(side_effect=ValueError("always fails"))

        with self.assertRaises(PosterError):
            poster.retry(fn)

        self.assertEqual(fn.call_count, 2)


class TestFacebookPoster(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.storage = MagicMock(spec=StorageInterface)
        self.bus = MagicMock(spec=EventBus)
        self.bm = MagicMock()
        self.bm.is_running = False
        self.bm.screenshot = MagicMock()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_platform_is_facebook(self) -> None:
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        self.assertEqual(poster.platform, PlatformType.FACEBOOK)

    def test_authenticate_already_logged_in(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if "feed" in sel or "What" in sel or "pagelet" in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        result = poster.authenticate()

        self.assertTrue(result)
        self.assertTrue(poster._authenticated)
        page.goto.assert_called_once()
        page.close.assert_called_once()

    def test_authenticate_not_logged_in_no_creds(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        self.bm.new_page.return_value = page

        poster = _make_poster(
            self.storage, self.bus, browser_manager=self.bm,
            fb_email=None, fb_password=None,
        )
        result = poster.authenticate()

        self.assertFalse(result)
        self.assertFalse(poster._authenticated)

    def test_authenticate_performs_login(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if "email" in sel or "pass" in sel or "login" in sel or "submit" in sel:
                return MagicMock()
            return None

        page.query_selector = qs

        logged_in_after = [False, True]
        orig_is_logged_in = FacebookPoster._is_logged_in

        with patch.object(
            FacebookPoster, "_is_logged_in", side_effect=lambda p: logged_in_after.pop(0),
        ):
            self.bm.new_page.return_value = page
            poster = _make_poster(
                self.storage, self.bus, browser_manager=self.bm,
                fb_email="test@test.com", fb_password="secret",
            )
            result = poster.authenticate()

        self.assertTrue(result)
        self.assertTrue(poster._authenticated)

    def test_post_success(self) -> None:
        page = _mock_page()
        page.url = "https://www.facebook.com/"
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = True

        with patch.object(
            FacebookPoster, "_is_logged_in", return_value=True,
        ):
            with patch.object(
                FacebookPoster, "_open_post_composer", return_value=None,
            ):
                with patch.object(
                    FacebookPoster, "_enter_post_text", return_value=None,
                ):
                    with patch.object(
                        FacebookPoster, "_publish", return_value="post_123",
                    ):
                        content = PostContent(text="Hello!")
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

        with patch.object(FacebookPoster, "_is_logged_in", return_value=True):
            with patch.object(FacebookPoster, "_open_post_composer", return_value=None):
                with patch.object(FacebookPoster, "_enter_post_text", return_value=None):
                    with patch.object(FacebookPoster, "_publish", return_value="post_456"):
                        content = PostContent(
                            text="With media",
                            media_paths=[str(media_file)],
                        )
                        post_id = poster.post(content)

        self.assertEqual(post_id, "post_456")
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
        page.url = "https://www.facebook.com/login/"
        result = FacebookPoster._is_logged_in(page)
        self.assertFalse(result)

    def test_is_logged_in_detects_feed(self) -> None:
        page = _mock_page()
        page.url = "https://www.facebook.com/"
        page.query_selector = MagicMock(return_value=MagicMock())
        result = FacebookPoster._is_logged_in(page)
        self.assertTrue(result)

    def test_publish_extracts_post_id_from_url(self) -> None:
        page = _mock_page()
        page.url = "https://www.facebook.com/username/posts/abc123def"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertEqual(post_id, "abc123def")

    def test_publish_extracts_story_fbid(self) -> None:
        page = _mock_page()
        page.url = "https://www.facebook.com/story.php?story_fbid=98765"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertEqual(post_id, "98765")

    def test_publish_fallback_when_no_id_found(self) -> None:
        page = _mock_page()
        page.url = "https://www.facebook.com/"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertTrue(post_id.startswith("fb_post_"))

    def test_dismiss_popups_handles_no_popups(self) -> None:
        page = _mock_page()
        page.query_selector_all.return_value = []
        FacebookPoster._dismiss_popups(page)

    def test_composer_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            FacebookPoster._open_post_composer(page)

    def test_text_area_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            FacebookPoster._enter_post_text(page, "test")

    def test_publish_button_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        with self.assertRaises(PosterError):
            poster._publish(page)
