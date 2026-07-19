import time
import re
from typing import Any, Optional, Union
from pathlib import Path

from playwright.sync_api import Page

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.posters.base import BasePoster, PostContent, PosterError
from golden_tier_external_world.browser import PlaywrightManager
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.events.bus import EventBus


IG_BASE_URL = "https://www.instagram.com"


class InstagramPoster(BasePoster):
    def __init__(
        self,
        storage: StorageInterface,
        event_bus: EventBus,
        browser_manager: Optional[PlaywrightManager] = None,
        user_data_dir: Optional[Union[str, Path]] = None,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        backoff_jitter: float = 0.5,
        headless: bool = True,
        screenshot_dir: Optional[Union[str, Path]] = None,
        page_load_timeout: int = 30000,
        ig_username: Optional[str] = None,
        ig_password: Optional[str] = None,
    ) -> None:
        if browser_manager is None and user_data_dir is not None:
            browser_manager = PlaywrightManager(
                user_data_dir=user_data_dir,
                headless=headless,
                screenshot_dir=screenshot_dir,
            )

        super().__init__(
            storage=storage,
            event_bus=event_bus,
            browser_manager=browser_manager,
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
            backoff_jitter=backoff_jitter,
            headless=headless,
            screenshot_dir=screenshot_dir,
        )
        self._page_load_timeout = page_load_timeout
        self._ig_username = ig_username
        self._ig_password = ig_password

    @property
    def platform(self) -> PlatformType:
        return PlatformType.INSTAGRAM

    def authenticate(self) -> bool:
        self._logger.info("Authenticating with Instagram")
        self._ensure_browser()
        page = self._browser.new_page()

        try:
            page.goto(IG_BASE_URL, timeout=self._page_load_timeout)
            time.sleep(4)

            if self._is_logged_in(page):
                self._authenticated = True
                self._logger.info("Already logged in via persistent session")
                return True

            self._logger.info("Session not found, attempting login")
            if not self._ig_username or not self._ig_password:
                self._logger.warning(
                    "No credentials provided for fallback login",
                )
                return False

            self._login(page)
            self._authenticated = True
            return True

        except Exception as e:
            self._logger.error("Authentication failed | error=%s", e)
            self._browser.screenshot(page, name="auth_failed")
            self._authenticated = False
            return False
        finally:
            page.close()

    def post(self, content: PostContent) -> str:
        self._logger.info(
            "Posting to Instagram | text_len=%d | media=%d",
            len(content.text),
            len(content.media_paths),
        )

        return self.retry(self._do_post, content)

    def _do_post(self, content: PostContent) -> str:
        self._ensure_authenticated()
        page = self._browser.new_page()

        try:
            page.goto(IG_BASE_URL, timeout=self._page_load_timeout)
            self._dismiss_popups(page)

            created = self._open_create_flow(page)
            if not created:
                raise PosterError("Could not open create flow")

            self._upload_media(page, content.media_paths)

            self._next_step(page)

            self._next_step(page)

            self._enter_caption(page, content.text)

            post_id = self._publish(page)

            self._logger.info("Post published | id=%s", post_id)
            return post_id

        except Exception:
            self._browser.screenshot(page, name="post_failed")
            raise
        finally:
            page.close()

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self._logger.info("Not authenticated; running authenticate()")
            if not self.authenticate():
                raise PosterError(
                    "Cannot post: authentication failed",
                    platform=self.platform.value,
                )

    @staticmethod
    def _is_logged_in(page: Page) -> bool:
        try:
            current_url = page.url
            if "login" in current_url or "accounts" in current_url:
                return False

            logged_in_selectors = [
                'svg[aria-label="Home"]',
                'svg[aria-label="Profile"]',
                'svg[aria-label="New post"]',
                'a[href="/direct/inbox/"]',
                'a[href="/"]',
                'div[role="navigation"]',
                'img[alt*="profile" i]',
                'header svg[aria-label="Home"]',
            ]
            for selector in logged_in_selectors:
                try:
                    el = page.query_selector(selector)
                    if el:
                        return True
                except Exception:
                    continue

            return False
        except Exception:
            return False

    def _login(self, page: Page) -> None:
        self._logger.info("Filling Instagram login form")
        try:
            username_sel = page.query_selector(
                'input[name="username"], input[autocomplete="username"]',
            )
            if username_sel:
                username_sel.fill(self._ig_username or "")
            else:
                raise PosterError(
                    "Username field not found",
                    platform=self.platform.value,
                )

            pass_sel = page.query_selector('input[name="password"]')
            if pass_sel:
                pass_sel.fill(self._ig_password or "")
            else:
                raise PosterError(
                    "Password field not found",
                    platform=self.platform.value,
                )

            login_btn = page.query_selector(
                'button[type="submit"], button:has-text("Log in")',
            )
            if login_btn:
                login_btn.click()
            else:
                raise PosterError(
                    "Login button not found",
                    platform=self.platform.value,
                )

            time.sleep(5)

            if not self._is_logged_in(page):
                self._browser.screenshot(page, name="login_failed")
                raise PosterError(
                    "Login failed after submitting credentials",
                    platform=self.platform.value,
                )

            self._logger.info("Login successful")
        except PosterError:
            raise
        except Exception as e:
            raise PosterError(
                f"Login failed: {e}",
                platform=self.platform.value,
            ) from e

    @staticmethod
    def _dismiss_popups(page: Page) -> None:
        dismiss_selectors = [
            'button:has-text("Not Now")',
            'button:has-text("Save Info")',
            'button:has-text("Turn On")',
            'button:has-text("Cancel")',
            'svg[aria-label="Close"]',
            'div[role="dialog"] button:has-text("Close")',
        ]
        for selector in dismiss_selectors:
            try:
                els = page.query_selector_all(selector)
                for el in els:
                    el.click()
                    time.sleep(0.5)
            except Exception:
                continue

    @staticmethod
    def _open_create_flow(page: Page) -> bool:
        create_selectors = [
            'svg[aria-label="New post"]',
            'a[href="/create"]',
        ]
        for selector in create_selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    el.click()
                    time.sleep(2)
                    return True
            except Exception:
                continue

        return False

    def _upload_media(self, page: Page, media_paths: list[Path]) -> None:
        if not media_paths:
            raise PosterError("No media files provided for upload")

        paths = [str(p) for p in media_paths if p.exists()]
        if not paths:
            raise PosterError("No valid media files found to upload")

        file_input_selectors = [
            'input[type="file"]',
            'input[accept*="image" i]',
            'input[accept*="video" i]',
            'form input[type="file"]',
        ]
        file_input = None
        for selector in file_input_selectors:
            try:
                file_input = page.query_selector(selector)
                if file_input:
                    break
            except Exception:
                continue

        if file_input:
            file_input.set_input_files(paths[:10])
            time.sleep(3)
            self._logger.info("Media uploaded | files=%d", len(paths[:10]))
        else:
            raise PosterError("Could not find file input for media upload")

    @staticmethod
    def _next_step(page: Page) -> None:
        next_selectors = [
            'div[role="button"]:has-text("Next")',
            'button:has-text("Next")',
            'div[role="button"]:has-text("Share")',
            'button:has-text("Share")',
        ]
        for selector in next_selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    el.click()
                    time.sleep(2)
                    return
            except Exception:
                continue

        raise PosterError("Could not find Next/Share button")

    @staticmethod
    def _enter_caption(page: Page, text: str) -> None:
        caption_selectors = [
            'textarea[aria-label="Write a caption..."]',
            'div[role="textbox"]',
            'textarea',
        ]
        caption_area = None
        for selector in caption_selectors:
            try:
                caption_area = page.query_selector(selector)
                if caption_area:
                    break
            except Exception:
                continue

        if caption_area:
            caption_area.fill(text)
            time.sleep(1)
        else:
            raise PosterError("Could not find caption text area")

    def _publish(self, page: Page) -> str:
        share_selectors = [
            'button:has-text("Share")',
            'div[role="button"]:has-text("Share")',
            'button:has-text("Post")',
        ]
        share_btn = None
        for selector in share_selectors:
            try:
                share_btn = page.query_selector(selector)
                if share_btn:
                    break
            except Exception:
                continue

        if not share_btn:
            raise PosterError("Could not find Share button")

        share_btn.click()
        time.sleep(5)

        current_url = page.url
        code_match = re.search(r'/p/([A-Za-z0-9_-]+)', current_url)
        if code_match:
            return code_match.group(1)

        reel_match = re.search(r'/reel/([A-Za-z0-9_-]+)', current_url)
        if reel_match:
            return reel_match.group(1)

        self._logger.warning(
            "Could not extract post ID from URL: %s",
            current_url,
        )
        return f"ig_post_{int(time.time())}"
