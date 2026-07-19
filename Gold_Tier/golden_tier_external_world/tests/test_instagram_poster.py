import tempfile
import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from golden_tier_external_world.posters import PostContent, PosterError
from golden_tier_external_world.posters.instagram import InstagramPoster
from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.events.bus import EventBus


def _mock_page() -> MagicMock:
    page = MagicMock()
    page.url = "https://www.instagram.com/"
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
) -> InstagramPoster:
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
    return InstagramPoster(**opts)


class TestInstagramPoster(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.storage = MagicMock(spec=StorageInterface)
        self.bus = MagicMock(spec=EventBus)
        self.bm = MagicMock()
        self.bm.is_running = False
        self.bm.screenshot = MagicMock()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_platform_is_instagram(self) -> None:
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        self.assertEqual(poster.platform, PlatformType.INSTAGRAM)

    def test_authenticate_already_logged_in(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if "Profile" in sel or "Home" in sel or 'href="/"' in sel or "navigation" in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        result = poster.authenticate()

        self.assertTrue(result)
        self.assertTrue(poster._authenticated)
        page.goto.assert_called_once_with("https://www.instagram.com", timeout=30000)
        page.close.assert_called_once()

    def test_authenticate_not_logged_in_no_creds(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        self.bm.new_page.return_value = page

        poster = _make_poster(
            self.storage, self.bus, browser_manager=self.bm,
            ig_username=None, ig_password=None,
        )
        result = poster.authenticate()

        self.assertFalse(result)
        self.assertFalse(poster._authenticated)

    def test_authenticate_performs_login(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if 'name="username"' in sel or 'autocomplete="username"' in sel:
                return MagicMock()
            if 'name="password"' in sel:
                return MagicMock()
            if 'button[type="submit"]' in sel or 'button:has-text("Log in")' in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        self.bm.new_page.return_value = page

        logged_in_after = [False, True]
        with patch.object(
            InstagramPoster, "_is_logged_in",
            side_effect=lambda p: logged_in_after.pop(0),
        ):
            poster = _make_poster(
                self.storage, self.bus, browser_manager=self.bm,
                ig_username="testuser", ig_password="secret",
            )
            result = poster.authenticate()

        self.assertTrue(result)
        self.assertTrue(poster._authenticated)

    def test_post_success(self) -> None:
        page = _mock_page()
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = True

        media_file = self.tmp_dir / "test.jpg"
        media_file.write_text("fake-image-data")

        with patch.object(InstagramPoster, "_open_create_flow", return_value=True):
            with patch.object(InstagramPoster, "_upload_media", return_value=None):
                with patch.object(InstagramPoster, "_next_step", return_value=None):
                    with patch.object(InstagramPoster, "_enter_caption", return_value=None):
                        with patch.object(InstagramPoster, "_publish", return_value="ABC123"):
                            content = PostContent(
                                text="Hello Instagram!",
                                media_paths=[str(media_file)],
                            )
                            post_id = poster.post(content)

        self.assertEqual(post_id, "ABC123")

    def test_post_raises_when_not_authenticated(self) -> None:
        page = _mock_page()
        self.bm.new_page.return_value = page

        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = False

        with self.assertRaises(PosterError):
            poster.post(PostContent(text="test", media_paths=["fake.jpg"]))

    def test_post_no_media_files_provided(self) -> None:
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        poster._authenticated = True

        with self.assertRaises(PosterError):
            poster.post(PostContent(text="no media"))

    def test_is_logged_in_detects_login_page(self) -> None:
        page = _mock_page()
        page.url = "https://www.instagram.com/accounts/login/"
        result = InstagramPoster._is_logged_in(page)
        self.assertFalse(result)

    def test_is_logged_in_detects_feed(self) -> None:
        page = _mock_page()
        page.url = "https://www.instagram.com/"
        page.query_selector = MagicMock(return_value=MagicMock())
        result = InstagramPoster._is_logged_in(page)
        self.assertTrue(result)

    def test_publish_extracts_post_code(self) -> None:
        page = _mock_page()
        page.url = "https://www.instagram.com/p/ABC123xyz/"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertEqual(post_id, "ABC123xyz")

    def test_publish_extracts_reel_code(self) -> None:
        page = _mock_page()
        page.url = "https://www.instagram.com/reel/DEF456uvw/"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertEqual(post_id, "DEF456uvw")

    def test_publish_fallback_when_no_id_found(self) -> None:
        page = _mock_page()
        page.url = "https://www.instagram.com/"
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        post_id = poster._publish(page)
        self.assertTrue(post_id.startswith("ig_post_"))

    def test_open_create_flow_returns_true_when_found(self) -> None:
        page = _mock_page()

        def qs(sel: str) -> MagicMock:
            if 'aria-label="New post"' in sel:
                return MagicMock()
            return None

        page.query_selector = qs
        result = InstagramPoster._open_create_flow(page)
        self.assertTrue(result)

    def test_open_create_flow_returns_false_when_not_found(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        result = InstagramPoster._open_create_flow(page)
        self.assertFalse(result)

    def test_next_step_raises_when_not_found(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            InstagramPoster._next_step(page)

    def test_caption_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        with self.assertRaises(PosterError):
            InstagramPoster._enter_caption(page, "test")

    def test_upload_media_raises_when_no_valid_files(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=MagicMock())
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        with self.assertRaises(PosterError):
            poster._upload_media(page, [Path("nonexistent.jpg")])

    def test_upload_media_raises_when_file_input_not_found(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        media_file = self.tmp_dir / "test.jpg"
        media_file.write_text("data")
        with self.assertRaises(PosterError):
            poster._upload_media(page, [media_file])

    def test_publish_button_not_found_raises(self) -> None:
        page = _mock_page()
        page.query_selector = MagicMock(return_value=None)
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)
        with self.assertRaises(PosterError):
            poster._publish(page)

    def test_upload_media_sets_files(self) -> None:
        page = _mock_page()
        file_input = MagicMock()
        file_input.set_input_files = MagicMock()

        def qs(sel: str) -> MagicMock:
            if 'input[type="file"]' in sel:
                return file_input
            return None

        page.query_selector = qs
        poster = _make_poster(self.storage, self.bus, browser_manager=self.bm)

        media_file = self.tmp_dir / "test.jpg"
        media_file.write_text("data")
        poster._upload_media(page, [media_file])

        file_input.set_input_files.assert_called_once_with([str(media_file)])

    def test_dismiss_popups_handles_no_popups(self) -> None:
        page = _mock_page()
        page.query_selector_all.return_value = []
        InstagramPoster._dismiss_popups(page)
