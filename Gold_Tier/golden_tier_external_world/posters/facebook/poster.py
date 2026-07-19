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


FB_BASE_URL = "https://www.facebook.com"


class FacebookPoster(BasePoster):
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
        fb_email: Optional[str] = None,
        fb_password: Optional[str] = None,
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
        self._fb_email = fb_email
        self._fb_password = fb_password

    @property
    def platform(self) -> PlatformType:
        return PlatformType.FACEBOOK

    def authenticate(self) -> bool:
        self._logger.info("Authenticating with Facebook")
        self._ensure_browser()
        page = self._browser.new_page()

        try:
            page.goto(FB_BASE_URL, timeout=self._page_load_timeout)
            time.sleep(3)

            if self._is_logged_in(page):
                self._authenticated = True
                self._logger.info("Already logged in via persistent session")
                return True

            self._logger.info("Session not found, attempting login")
            if not self._fb_email or not self._fb_password:
                self._logger.warning(
                    "No credentials provided for fallback login"
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
            "Posting to Facebook | text_len=%d | media=%d",
            len(content.text),
            len(content.media_paths),
        )

        return self.retry(self._do_post, content)

    def _do_post(self, content: PostContent) -> str:
        self._ensure_authenticated()
        page = self._browser.new_page()

        try:
            page.goto(FB_BASE_URL, timeout=self._page_load_timeout)
            self._dismiss_popups(page)

            self._open_post_composer(page)

            self._enter_post_text(page, content.text)

            if content.media_paths:
                self._upload_media(page, content.media_paths)

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
            if "login" in current_url or "checkpoint" in current_url:
                return False

            feed_selectors = [
                'div[role="feed"]',
                'input[aria-label="What\'s on your mind?"]',
                'div[aria-label="What\'s on your mind?"]',
                '[data-pagelet="Feed"]',
                'a[aria-label="Facebook"]',
                'div[data-pagelet="Stories"]',
            ]
            for selector in feed_selectors:
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
        self._logger.info("Filling login form")
        try:
            email_sel = page.query_selector('input[id="email"], input[name="email"], input[type="text"]')
            if email_sel:
                email_sel.fill(self._fb_email or "")
            else:
                raise PosterError("Email field not found", platform=self.platform.value)

            pass_sel = page.query_selector('input[id="pass"], input[name="pass"], input[type="password"]')
            if pass_sel:
                pass_sel.fill(self._fb_password or "")
            else:
                raise PosterError("Password field not found", platform=self.platform.value)

            login_btn = page.query_selector('button[name="login"], button[type="submit"]')
            if login_btn:
                login_btn.click()
            else:
                raise PosterError("Login button not found", platform=self.platform.value)

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
            'div[aria-label="Close"]',
            'div[aria-label="Close"] button',
            'svg[aria-label="Close"]',
            'button:has-text("Not Now")',
            'button:has-text("Close")',
            'div[role="dialog"] button:has-text("OK")',
        ]
        for selector in dismiss_selectors:
            try:
                buttons = page.query_selector_all(selector)
                for btn in buttons:
                    btn.click()
                    time.sleep(0.5)
            except Exception:
                continue

    @staticmethod
    def _open_post_composer(page: Page) -> None:
        composer_selectors = [
            'input[aria-label="What\'s on your mind?"]',
            'div[aria-label="What\'s on your mind?"]',
            'span:has-text("What\'s on your mind")',
            'div[role="button"]:has-text("What\'s on your mind")',
            'a[aria-label="Create post"]',
            'div[data-pagelet*="Composer"]',
        ]
        for selector in composer_selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    el.click()
                    time.sleep(2)
                    return
            except Exception:
                continue

        raise PosterError("Could not open post composer")

    @staticmethod
    def _enter_post_text(page: Page, text: str) -> None:
        text_area_selectors = [
            'div[aria-label*="What\'s on your mind"]',
            'div[aria-label="What\'s on your mind?"]',
            'div[contenteditable="true"][role="textbox"]',
            'div[role="textbox"]',
            '[data-lexical-editor]',
        ]
        text_area = None
        for selector in text_area_selectors:
            try:
                text_area = page.query_selector(selector)
                if text_area:
                    break
            except Exception:
                continue

        if text_area:
            text_area.fill(text)
            time.sleep(1)
        else:
            raise PosterError("Could not find post text area")

    def _upload_media(self, page: Page, media_paths: list[Path]) -> None:
        file_input_selectors = [
            'input[type="file"][accept*="image" i]',
            'input[type="file"][accept*="video" i]',
            'input[type="file"][accept*="media" i]',
            'input[type="file"][multiple]',
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
            paths = [str(p) for p in media_paths if p.exists()]
            if not paths:
                raise PosterError("No valid media files found to upload")

            file_input.set_input_files(paths)
            time.sleep(3)
            self._logger.info("Media uploaded | files=%d", len(paths))
        else:
            raise PosterError(
                "Could not find file input for media upload",
            )

    def _publish(self, page: Page) -> str:
        publish_selectors = [
            'div[aria-label="Post"]',
            'div[aria-label="Post"] button',
            'button:has-text("Post")',
            'div[role="button"]:has-text("Post")',
            'span:has-text("Post")',
        ]
        publish_btn = None
        for selector in publish_selectors:
            try:
                publish_btn = page.query_selector(selector)
                if publish_btn:
                    break
            except Exception:
                continue

        if not publish_btn:
            raise PosterError("Could not find publish button")

        publish_btn.click()
        time.sleep(3)

        current_url = page.url
        post_match = re.search(r'/posts/([a-zA-Z0-9_.-]+)', current_url)
        if post_match:
            return post_match.group(1)

        story_match = re.search(r'/story\.php\?story_fbid=(\d+)', current_url)
        if story_match:
            return story_match.group(1)

        fb_id_match = re.search(r'fbid=(\d+)', current_url)
        if fb_id_match:
            return fb_id_match.group(1)

        self._logger.warning(
            "Could not extract post ID from URL: %s",
            current_url,
        )
        return f"fb_post_{int(time.time())}"
