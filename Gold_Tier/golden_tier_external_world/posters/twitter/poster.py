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


TWITTER_BASE_URL = "https://x.com"


class TwitterPoster(BasePoster):
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
        twitter_username: Optional[str] = None,
        twitter_password: Optional[str] = None,
        twitter_email: Optional[str] = None,
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
        self._twitter_username = twitter_username
        self._twitter_password = twitter_password
        self._twitter_email = twitter_email

    @property
    def platform(self) -> PlatformType:
        return PlatformType.TWITTER

    def authenticate(self) -> bool:
        self._logger.info("Authenticating with Twitter/X")
        self._ensure_browser()
        page = self._browser.new_page()

        try:
            page.goto(TWITTER_BASE_URL, timeout=self._page_load_timeout)
            time.sleep(4)

            if self._is_logged_in(page):
                self._authenticated = True
                self._logger.info("Already logged in via persistent session")
                return True

            self._logger.info("Session not found, attempting login")
            if not self._twitter_username or not self._twitter_password:
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
            "Posting to Twitter/X | text_len=%d | media=%d",
            len(content.text),
            len(content.media_paths),
        )

        return self.retry(self._do_post, content)

    def _do_post(self, content: PostContent) -> str:
        self._ensure_authenticated()
        page = self._browser.new_page()

        try:
            page.goto(TWITTER_BASE_URL, timeout=self._page_load_timeout)
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
            if "login" in current_url or "i/flow" in current_url:
                return False

            logged_in_selectors = [
                'a[aria-label="Post"]',
                'a[aria-label="Profile"]',
                'div[data-testid="SideNav_NewTweet_Button"]',
                'div[data-testid="primaryColumn"]',
                'div[aria-label="Home timeline"]',
                '[data-testid="tweetTextarea_0"]',
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
        self._logger.info("Filling Twitter/X login form")
        try:
            self._fill_login_step(page, "username", self._twitter_username or "")

            next_btn = page.query_selector(
                'button:has-text("Next"), div[role="button"]:has-text("Next")',
            )
            if next_btn:
                next_btn.click()
                time.sleep(3)

            unusual_login = page.query_selector('input[name="text"]')
            if unusual_login and self._twitter_email:
                self._logger.info("Unusual login detected, providing email")
                unusual_login.fill(self._twitter_email)
                next_btn2 = page.query_selector(
                    'button:has-text("Next"), div[role="button"]:has-text("Next")',
                )
                if next_btn2:
                    next_btn2.click()
                    time.sleep(2)

            self._fill_login_step(page, "password", self._twitter_password or "")

            login_btn = page.query_selector(
                'button:has-text("Log in"), div[role="button"]:has-text("Log in")',
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
    def _fill_login_step(page: Page, field_name: str, value: str) -> None:
        attr_selectors = [
            f'input[name="{field_name}"]',
            f'input[autocomplete="{field_name}"]',
            f'input[type="{field_name}"]',
        ]
        if field_name == "username":
            attr_selectors.extend([
                'input[autocomplete="username"]',
                'input[name="text"]',
            ])
        elif field_name == "password":
            attr_selectors.append('input[type="password"]')

        for selector in attr_selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    el.fill(value)
                    return
            except Exception:
                continue

        raise PosterError(
            f"Could not find {field_name} field",
            platform="twitter",
        )

    @staticmethod
    def _dismiss_popups(page: Page) -> None:
        dismiss_selectors = [
            'div[aria-label="Close"]',
            'button:has-text("Not now")',
            'button:has-text("Skip")',
            'div[role="dialog"] button:has-text("Close")',
            'svg[aria-label="Close"]',
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
    def _open_post_composer(page: Page) -> None:
        composer_selectors = [
            'a[aria-label="Post"]',
            'div[data-testid="SideNav_NewTweet_Button"]',
            'div[aria-label="Post"]',
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
            'div[data-testid="tweetTextarea_0"]',
            'div[role="textbox"]',
            '[contenteditable="true"]',
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
            raise PosterError("Could not find tweet text area")

    def _upload_media(self, page: Page, media_paths: list[Path]) -> None:
        file_input_selectors = [
            'input[type="file"][accept*="image" i]',
            'input[type="file"][accept*="video" i]',
            'input[type="file"][accept*="media" i]',
            'input[type="file"][multiple]',
            'input[type="file"]',
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
            media_btn = page.query_selector(
                'input[type="file"], div[data-testid="fileInput"]',
            )
            if media_btn:
                paths = [str(p) for p in media_paths if p.exists()]
                if not paths:
                    raise PosterError("No valid media files found to upload")
                media_btn.set_input_files(paths)
                time.sleep(3)
                self._logger.info("Media uploaded | files=%d", len(paths))
            else:
                raise PosterError(
                    "Could not find file input for media upload",
                )

    def _publish(self, page: Page) -> str:
        publish_selectors = [
            'div[data-testid="tweetButton"]',
            'div[data-testid="tweetButtonInline"]',
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
        time.sleep(4)

        current_url = page.url
        status_match = re.search(r'/status/(\d+)', current_url)
        if status_match:
            return status_match.group(1)

        self._logger.warning(
            "Could not extract tweet ID from URL: %s",
            current_url,
        )
        return f"tw_post_{int(time.time())}"
