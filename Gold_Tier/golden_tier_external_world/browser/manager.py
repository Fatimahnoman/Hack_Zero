import os
import re
import socket
import subprocess
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Optional, Union

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

_CHROME_CANDIDATES = [
    os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


class BrowserError(Exception):
    """Raised when a browser operation fails."""


class PlaywrightManager:
    def __init__(
        self,
        user_data_dir: Optional[Union[str, Path]] = None,
        headless: bool = True,
        viewport: Optional[dict[str, int]] = None,
        locale: str = "en-US",
        timeout: int = 30000,
        tracing_dir: Optional[Union[str, Path]] = None,
        screenshot_dir: Optional[Union[str, Path]] = None,
        restart_max_attempts: int = 3,
        restart_base_delay: float = 2.0,
        cdp_port: int = 9222,
        **context_kwargs: Any,
    ) -> None:
        self._user_data_dir = str(user_data_dir) if user_data_dir else None
        self._headless = headless
        self._viewport = viewport or {"width": 1280, "height": 720}
        self._locale = locale
        self._timeout = timeout
        self._tracing_dir = Path(tracing_dir) if tracing_dir else None
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._restart_max_attempts = restart_max_attempts
        self._restart_base_delay = restart_base_delay
        self._cdp_port = cdp_port
        self._context_kwargs = context_kwargs

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._browser: Optional[Browser] = None
        self._chrome_process: Optional[subprocess.Popen] = None
        self._running = False
        self._logger = logging.getLogger(self.__class__.__name__)

        if self._tracing_dir:
            self._tracing_dir.mkdir(parents=True, exist_ok=True)
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        if self._user_data_dir and not os.path.exists(self._user_data_dir):
            os.makedirs(self._user_data_dir, exist_ok=True)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise BrowserError("Browser not started. Call start() first.")
        return self._context

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise BrowserError("Browser not started. Call start() first.")
        return self._browser

    def start(self) -> None:
        if self._running:
            self._logger.warning("Already running")
            return
        self._logger.info(
            "Starting Playwright manager | headless=%s | user_data_dir=%s",
            self._headless,
            self._user_data_dir or "(none)",
        )
        self._launch()

    def stop(self) -> None:
        if not self._running:
            return
        self._logger.info("Stopping browser manager")
        self._close()

    def restart(self) -> None:
        self._logger.info("Restarting browser manager")
        self._close()
        self._launch()

    def __enter__(self) -> "PlaywrightManager":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def new_page(self, **kwargs: Any) -> Page:
        page = self.context.new_page(**kwargs)
        page.set_default_timeout(self._timeout)
        self._logger.debug("New page created | total=%d", len(self.context.pages))
        return page

    def safe_goto(
        self,
        url: str,
        *,
        screenshot_on_fail: bool = True,
        **kwargs: Any,
    ) -> Optional[Page]:
        page = self.new_page()
        try:
            page.goto(url, **kwargs)
            return page
        except Exception:
            if screenshot_on_fail:
                self.screenshot(page)
            raise

    def screenshot(
        self,
        page: Optional[Page] = None,
        name: Optional[str] = None,
    ) -> Optional[Path]:
        if self._screenshot_dir is None:
            self._logger.debug("Screenshot directory not configured; skipping")
            return None

        try:
            p = page or (self.context.pages[-1] if self.context.pages else None)
            if p is None:
                self._logger.warning("No page available for screenshot")
                return None

            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            label = name or "failure"
            filename = f"{label}_{ts}.png"
            filepath = self._screenshot_dir / filename

            p.screenshot(path=str(filepath), full_page=True)
            self._logger.info("Screenshot saved | path=%s", filepath)
            return filepath
        except Exception as e:
            self._logger.warning("Failed to capture screenshot | error=%s", e)
            return None

    def start_tracing(
        self,
        name: Optional[str] = None,
        screenshots: bool = True,
        snapshots: bool = True,
    ) -> None:
        if self._tracing_dir is None:
            self._logger.debug("Tracing directory not configured; skipping")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        label = name or "trace"
        self._tracing_name = f"{label}_{ts}"
        self.context.tracing.start(
            screenshots=screenshots,
            snapshots=snapshots,
            name=self._tracing_name,
        )
        self._logger.info("Tracing started | name=%s", self._tracing_name)

    def stop_tracing(self, name: Optional[str] = None) -> Optional[Path]:
        if self._tracing_dir is None:
            return None

        label = name or getattr(self, "_tracing_name", "trace")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{label}_{ts}.zip"
        filepath = self._tracing_dir / filename

        try:
            self.context.tracing.stop(path=str(filepath))
            self._logger.info("Tracing saved | path=%s", filepath)
            return filepath
        except Exception as e:
            self._logger.warning("Failed to stop tracing | error=%s", e)
            return None

    def _find_chrome(self) -> str:
        for path in _CHROME_CANDIDATES:
            if os.path.exists(path):
                self._logger.debug("Found system Chrome at %s", path)
                return path

        pw_dir = os.path.expandvars(
            r"%USERPROFILE%\AppData\Local\ms-playwright"
        )
        if os.path.isdir(pw_dir):
            for entry in sorted(os.listdir(pw_dir), reverse=True):
                candidate = os.path.join(pw_dir, entry, "chrome-win64", "chrome.exe")
                if os.path.exists(candidate):
                    self._logger.debug("Found Playwright Chromium at %s", candidate)
                    return candidate

        raise BrowserError(
            "Chrome executable not found. Install Google Chrome or run: playwright install chromium"
        )

    def _wait_for_port(self, timeout: float = 15) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._cdp_port), timeout=1):
                    return True
            except (ConnectionRefusedError, OSError):
                time.sleep(0.5)
        return False

    def _kill_process_on_port(self) -> None:
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if f":{self._cdp_port}" not in line:
                    continue
                m = re.search(r"(\d+)\s*$", line.strip())
                if m:
                    pid = m.group(1)
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=3,
                        )
                        self._logger.info("Killed process %s on port %d", pid, self._cdp_port)
                    except Exception:
                        pass
        except Exception:
            pass

    def _launch(self) -> None:
        if not self._user_data_dir:
            raise BrowserError("user_data_dir is required for subprocess-based launch")

        if not os.path.exists(self._user_data_dir):
            os.makedirs(self._user_data_dir, exist_ok=True)

        self._kill_process_on_port()
        chrome_path = self._find_chrome()

        args = [
            chrome_path,
            f"--user-data-dir={self._user_data_dir}",
            f"--remote-debugging-port={self._cdp_port}",
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
        ]
        if self._headless:
            args.append("--headless=new")

        self._logger.info("Launching Chrome: %s", " ".join(args))
        env = os.environ.copy()
        env["NODE_OPTIONS"] = "--no-deprecation"
        try:
            self._chrome_process = subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env,
            )
        except FileNotFoundError:
            raise BrowserError(f"Chrome executable not found at: {chrome_path}")

        if not self._wait_for_port():
            self._chrome_process.kill()
            raise BrowserError(
                f"Chrome did not start on port {self._cdp_port} within timeout"
            )

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self._cdp_port}",
        )

        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = self._browser.new_context()

        if self._context_kwargs:
            for k, v in self._context_kwargs.items():
                try:
                    setattr(self._context, k, v)
                except Exception:
                    self._logger.warning(
                        "Could not set context attribute %s", k
                    )

        self._running = True
        self._logger.info(
            "Browser launched via CDP | port=%d | chrome=%s",
            self._cdp_port, chrome_path,
        )

    def _close(self) -> None:
        if self._chrome_process:
            try:
                self._chrome_process.kill()
                self._chrome_process.wait(timeout=5)
            except Exception as e:
                self._logger.warning("Error killing Chrome process | error=%s", e)
            self._chrome_process = None

        try:
            if self._context:
                self._context.close()
        except Exception as e:
            self._logger.warning("Error closing context | error=%s", e)

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            self._logger.warning("Error stopping playwright | error=%s", e)

        self._context = None
        self._browser = None
        self._playwright = None
        self._running = False

    def _is_browser_alive(self) -> bool:
        if not self._context:
            return False
        try:
            _ = self._context.pages
            return True
        except Exception:
            return False

    def recover(self) -> None:
        self._logger.info("Attempting browser recovery")
        for attempt in range(1, self._restart_max_attempts + 1):
            delay = self._restart_base_delay * (2 ** (attempt - 1))
            self._logger.info(
                "Recovery attempt %d/%d | waiting %.1fs",
                attempt,
                self._restart_max_attempts,
                delay,
            )
            time.sleep(delay)
            try:
                self.restart()
                if self._is_browser_alive():
                    self._logger.info("Recovery successful")
                    return
            except Exception as e:
                self._logger.warning(
                    "Recovery attempt %d failed | error=%s",
                    attempt,
                    e,
                )
        self._logger.error("All recovery attempts exhausted")
        raise BrowserError(
            "Browser recovery failed after %d attempts" % self._restart_max_attempts,
        )

    def ensure_alive(self) -> None:
        if not self._is_browser_alive():
            self._logger.warning("Browser not alive; initiating recovery")
            self.recover()
