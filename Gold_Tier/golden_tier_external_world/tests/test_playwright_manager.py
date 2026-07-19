import tempfile
import shutil
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock, ANY

from golden_tier_external_world.browser import PlaywrightManager, BrowserError


def _mock_launch_deps(mgr, mock_chrome="C:\\chrome.exe"):
    patch_find = patch.object(mgr, "_find_chrome", return_value=mock_chrome)
    patch_port = patch.object(mgr, "_wait_for_port", return_value=True)
    patch_kill = patch.object(mgr, "_kill_process_on_port")
    patch_popen = patch("golden_tier_external_world.browser.manager.subprocess.Popen")
    patch_sp = patch("golden_tier_external_world.browser.manager.sync_playwright")

    find_mock = patch_find.start()
    port_mock = patch_port.start()
    kill_mock = patch_kill.start()
    popen_mock = patch_popen.start()
    sp_mock = patch_sp.start()

    return {
        "find": find_mock,
        "port": port_mock,
        "kill": kill_mock,
        "popen": popen_mock,
        "sp": sp_mock,
        "patchers": [patch_find, patch_port, patch_kill, patch_popen, patch_sp],
    }


class TestPlaywrightManager(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.screenshot_dir = self.tmp_dir / "screenshots"
        self.tracing_dir = self.tmp_dir / "traces"
        self.user_data_dir = self.tmp_dir / "profile"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _setup_sp_mock(self, sp_mock, context=None):
        mock_playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = context or MagicMock()
        mock_context.browser = MagicMock()
        mock_browser.contexts = [mock_context]
        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp.return_value = mock_browser
        mock_playwright.chromium = mock_chromium
        sp_mock.return_value.start.return_value = mock_playwright
        return mock_playwright, mock_browser, mock_context, mock_chromium

    def test_start_launches_browser(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, _, mock_context, mock_chromium = self._setup_sp_mock(mock_sp)

        mgr.start()

        self.assertTrue(mgr.is_running)
        self.assertIs(mgr.context, mock_context)
        deps["popen"].assert_called_once()
        mock_chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9222",
        )
        for p in deps["patchers"]:
            p.stop()

    def test_stop_closes_context(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, _, mock_context, _ = self._setup_sp_mock(mock_sp)

        mgr.start()
        mgr.stop()

        self.assertFalse(mgr.is_running)
        mock_context.close.assert_called_once()
        for p in deps["patchers"]:
            p.stop()

    def test_double_start_is_noop(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, _, _, mock_chromium = self._setup_sp_mock(mock_sp)

        mgr.start()
        mgr.start()

        mock_chromium.connect_over_cdp.assert_called_once()
        for p in deps["patchers"]:
            p.stop()

    def test_pass_user_data_dir(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)

        mgr.start()

        popen_args = deps["popen"].call_args[0][0]
        self.assertIn(f"--user-data-dir={self.user_data_dir}", popen_args)
        for p in deps["patchers"]:
            p.stop()

    def test_new_page_creates_page(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_context.browser = MagicMock()
        mock_context.pages = []
        _, _, _, _ = self._setup_sp_mock(mock_sp, context=mock_context)

        mgr.start()
        page = mgr.new_page()

        mock_context.new_page.assert_called_once()
        mock_page.set_default_timeout.assert_called_once_with(30000)
        self.assertIs(page, mock_page)
        for p in deps["patchers"]:
            p.stop()

    def test_screenshot_saves_file(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            screenshot_dir=self.screenshot_dir,
        )
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.pages = [mock_page]
        mock_context.browser = MagicMock()
        _, _, _, _ = self._setup_sp_mock(mock_sp, context=mock_context)

        mgr.start()
        path = mgr.screenshot(page=mock_page, name="test_shot")

        self.assertIsNotNone(path)
        self.assertTrue(str(path).startswith(str(self.screenshot_dir)))
        self.assertIn("test_shot", str(path))
        self.assertTrue(str(path).endswith(".png"))
        mock_page.screenshot.assert_called_once()
        for p in deps["patchers"]:
            p.stop()

    def test_screenshot_no_dir_skips(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        mock_context = MagicMock()
        mock_context.pages = []
        mock_context.browser = MagicMock()
        self._setup_sp_mock(mock_sp, context=mock_context)

        mgr.start()
        result = mgr.screenshot(name="test")
        self.assertIsNone(result)
        for p in deps["patchers"]:
            p.stop()

    def test_tracing(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            tracing_dir=self.tracing_dir,
        )
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        mock_context = MagicMock()
        mock_context.browser = MagicMock()
        _, _, _, _ = self._setup_sp_mock(mock_sp, context=mock_context)

        mgr.start()
        mgr.start_tracing(name="test_trace")
        path = mgr.stop_tracing()

        self.assertIsNotNone(path)
        self.assertTrue(str(path).startswith(str(self.tracing_dir)))
        self.assertTrue(str(path).endswith(".zip"))
        mock_context.tracing.start.assert_called_once()
        mock_context.tracing.stop.assert_called_once_with(path=ANY)
        for p in deps["patchers"]:
            p.stop()

    def test_context_manager(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, _, mock_context, _ = self._setup_sp_mock(mock_sp)

        with mgr as m:
            self.assertTrue(m.is_running)

        self.assertFalse(m.is_running)
        for p in deps["patchers"]:
            p.stop()

    def test_restart(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, _, mock_context, mock_chromium = self._setup_sp_mock(mock_sp)

        mgr.start()
        mgr.restart()

        self.assertEqual(mock_chromium.connect_over_cdp.call_count, 2)
        self.assertEqual(deps["popen"].call_count, 2)
        for p in deps["patchers"]:
            p.stop()

    def test_ensure_alive_when_dead(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            restart_max_attempts=1,
            restart_base_delay=0.01,
        )
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        mock_context1 = MagicMock()
        mock_context1.browser = MagicMock()
        mock_context2 = MagicMock()
        mock_context2.browser = MagicMock()

        mock_playwright = MagicMock()
        mock_browser1 = MagicMock()
        mock_browser1.contexts = [mock_context1]
        mock_browser2 = MagicMock()
        mock_browser2.contexts = [mock_context2]
        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp.side_effect = [
            mock_browser1,
            mock_browser2,
        ]
        mock_playwright.chromium = mock_chromium
        mock_sp.return_value.start.return_value = mock_playwright

        mgr.start()

        with patch.object(mgr, "_is_browser_alive", side_effect=[False, True]):
            mgr.ensure_alive()

        self.assertTrue(mgr.is_running)
        self.assertEqual(mgr.context, mock_context2)
        for p in deps["patchers"]:
            p.stop()

    def test_ensure_alive_when_healthy(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, _, mock_context, mock_chromium = self._setup_sp_mock(mock_sp)

        mgr.start()

        with patch.object(mgr, "_is_browser_alive", return_value=True):
            mgr.ensure_alive()

        mock_chromium.connect_over_cdp.assert_called_once()
        for p in deps["patchers"]:
            p.stop()

    def test_recover_all_attempts_fail(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            restart_max_attempts=2,
            restart_base_delay=0.01,
        )

        with (
            patch.object(mgr, "_find_chrome", return_value="C:\\chrome.exe"),
            patch.object(mgr, "_wait_for_port", return_value=True),
            patch.object(mgr, "_kill_process_on_port"),
            patch("golden_tier_external_world.browser.manager.subprocess.Popen"),
            patch(
                "golden_tier_external_world.browser.manager.sync_playwright",
                side_effect=Exception("pw fail"),
            ),
        ):
            with self.assertRaises(BrowserError):
                mgr.recover()

    def test_safe_goto_takes_screenshot_on_fail(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            screenshot_dir=self.screenshot_dir,
        )
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        mock_page = MagicMock()
        mock_page.goto.side_effect = Exception("timeout")
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_context.browser = MagicMock()
        self._setup_sp_mock(mock_sp, context=mock_context)

        mgr.start()

        with self.assertRaises(Exception):
            mgr.safe_goto("https://example.com")

        mock_page.screenshot.assert_called_once()
        for p in deps["patchers"]:
            p.stop()

    def test_launch_failure_raises_browser_error(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            restart_max_attempts=0,
        )

        with (
            patch.object(mgr, "_find_chrome", return_value="C:\\chrome.exe"),
            patch.object(mgr, "_wait_for_port", return_value=False),
            patch.object(mgr, "_kill_process_on_port"),
            patch("golden_tier_external_world.browser.manager.subprocess.Popen"),
        ):
            with self.assertRaises(BrowserError):
                mgr.start()

        self.assertFalse(mgr.is_running)

    def test_context_property_raises_when_not_started(self):
        mgr = PlaywrightManager()

        with self.assertRaises(BrowserError):
            _ = mgr.context

    def test_browser_property_returns_browser(self):
        mgr = PlaywrightManager(user_data_dir=self.user_data_dir)
        deps = _mock_launch_deps(mgr)
        mock_sp = deps["sp"]
        _, mock_browser, _, _ = self._setup_sp_mock(mock_sp)

        mgr.start()

        self.assertIs(mgr.browser, mock_browser)
        for p in deps["patchers"]:
            p.stop()

    def test_context_kwargs_stored(self):
        mgr = PlaywrightManager(
            user_data_dir=self.user_data_dir,
            extra_http_headers={"X-Test": "1"},
        )

        self.assertIn("extra_http_headers", mgr._context_kwargs)
        self.assertEqual(mgr._context_kwargs["extra_http_headers"], {"X-Test": "1"})
