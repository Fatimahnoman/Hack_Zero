import json
import time
import re
from typing import Optional, Union
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

from golden_tier_external_world.watchers.base import BaseWatcher
from golden_tier_external_world.events.models import (
    BaseEvent,
    MessageEvent,
    CommentEvent,
    MentionEvent,
    LikeEvent,
    FollowEvent,
)
from golden_tier_external_world.models.platform import PlatformAccount
from golden_tier_external_world.models.content import ContentItem
from golden_tier_external_world.config.enums import ContentCategory, WatcherState
from golden_tier_external_world.browser import PlaywrightManager
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.utils.captcha import ReCaptchaSolver
from golden_tier_external_world.config.secrets import get_secret


IG_BASE_URL = "https://www.instagram.com"


class InstagramWatcher(BaseWatcher):
    def __init__(
        self,
        *args,
        browser_manager: Optional[PlaywrightManager] = None,
        user_data_dir: Optional[Union[str, Path]] = None,
        headless: bool = True,
        screenshot_dir: Optional[Union[str, Path]] = None,
        seen_vault: Optional[SeenVault] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        captcha_solver: Optional[ReCaptchaSolver] = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._browser: Optional[PlaywrightManager] = browser_manager
        self._user_data_dir = str(user_data_dir) if user_data_dir else None
        self._headless = headless
        self._screenshot_dir = screenshot_dir
        self._authenticated: bool = False

        self._username = username or get_secret("INSTAGRAM_EMAIL")
        self._password = password or get_secret("INSTAGRAM_PASSWORD")
        self._captcha_solver = captcha_solver or ReCaptchaSolver()

        if seen_vault is not None:
            self._seen = seen_vault
        else:
            backend = JsonBackend(
                self._config.platform.value.replace(" ", "_") + "_seen.json",
                auto_create=True,
            )
            self._seen = SeenVault(backend)

    @property
    def browser(self) -> PlaywrightManager:
        if self._browser is None:
            raise RuntimeError("Browser not initialized. Call authenticate() first.")
        return self._browser

    def _ensure_browser(self) -> None:
        if self._browser is None:
            self._browser = PlaywrightManager(
                user_data_dir=self._user_data_dir,
                headless=self._headless,
                screenshot_dir=self._screenshot_dir,
            )
        if not self._browser.is_running:
            self._browser.start()

    def _try_restore_cookies(self) -> None:
        if not self._user_data_dir:
            return
        cookies_path = Path(self._user_data_dir) / "cookies_backup.json"
        if not cookies_path.exists():
            self._logger.debug("No cookies_backup.json found at %s", cookies_path)
            return
        try:
            with open(cookies_path, "r") as f:
                cookies = json.load(f)
            if not cookies:
                return
            self._browser.context.add_cookies(cookies)
            self._logger.info("Restored %d cookies from saved session", len(cookies))
        except Exception as e:
            self._logger.warning("Failed to restore cookies: %s", e)

    def _detect_challenge(self, page: Page) -> bool:
        url = page.url
        if "challenge" in url:
            return True
        selectors = [
            'iframe[src*="challenge"]',
            'div[data-testid="challenge"]',
            'form[action*="challenge"]',
            'h2:has-text("Confirm Your Identity")',
            'h2:has-text("Verify")',
            'div:has-text("We noticed a login")',
        ]
        for sel in selectors:
            try:
                if page.query_selector(sel):
                    return True
            except Exception:
                continue
        return False

    def authenticate(self) -> bool:
        self._logger.info("Starting Instagram authentication...")
        self._ensure_browser()
        page = self._browser.new_page()

        try:
            self._try_restore_cookies()

            page.goto(IG_BASE_URL, timeout=30000, wait_until="domcontentloaded")
            time.sleep(2)

            page.reload(wait_until="domcontentloaded")
            time.sleep(3)

            if self._is_logged_in(page):
                self._dismiss_popups(page)
                self._authenticated = True
                self._logger.info("Logged in via saved session")
                return True

            if self._detect_challenge(page):
                self._logger.info("Challenge page detected after cookie restore")
                if self._verify_identity(page):
                    self._dismiss_popups(page)
                    self._save_session_cookies()
                    self._authenticated = True
                    self._logger.info("Challenge solved, logged in")
                    return True

            if self._username and self._password:
                self._logger.info("Attempting auto-login with saved credentials")
                if self._auto_login(page):
                    self._dismiss_popups(page)
                    self._save_session_cookies()
                    self._authenticated = True
                    self._logger.info("Auto-login successful")
                    return True

            if self._detect_challenge(page):
                self._logger.info("Challenge detected after login attempt")
                if self._verify_identity(page):
                    self._dismiss_popups(page)
                    self._save_session_cookies()
                    self._authenticated = True
                    self._logger.info("Challenge solved, logged in")
                    return True

            self._logger.info("Waiting for manual login in browser...")
            self._browser.screenshot(page, name="login_needed")
            result = self._wait_for_manual_login()
            if result:
                self._dismiss_popups(page)
                self._save_session_cookies()
            return result

        except Exception as e:
            self._logger.error("Authentication failed: %s", e)
            self._browser.screenshot(page, name="auth_failed")
            self._state = WatcherState.ERROR
            self._authenticated = False
            return False
        finally:
            page.close()

    def _auto_login(self, page: Page) -> bool:
        try:
            current_url = page.url
            if "login" not in current_url and "accounts" not in current_url:
                login_link = page.query_selector('a[href*="login"]')
                if login_link and login_link.is_visible():
                    login_link.click()
                    time.sleep(3)
                else:
                    page.goto(f"{IG_BASE_URL}/accounts/login/", timeout=30000)
                    time.sleep(4)

            self._dismiss_popups(page)

            for _ in range(10):
                username_input = page.query_selector('input[name="username"]')
                password_input = page.query_selector('input[name="password"]')
                if username_input and password_input:
                    break
                time.sleep(1)

            if not username_input or not password_input:
                self._logger.warning("Login form not found")
                return False

            username_input.fill(self._username)
            time.sleep(0.5)
            password_input.fill(self._password)
            time.sleep(0.5)

            self._captcha_solver.detect_and_solve(page, timeout=120)

            login_btn = (
                page.query_selector('button[type="submit"]')
                or page.query_selector('button:has-text("Log In")')
                or page.query_selector('div[role="button"]:has-text("Log In")')
            )
            if login_btn:
                login_btn.click()
            else:
                page.keyboard.press("Enter")

            self._logger.info("Login submitted, waiting for response...")

            for _ in range(30):
                time.sleep(1)
                try:
                    if self._is_logged_in(page):
                        self._dismiss_popups(page)
                        return True
                    if self._detect_challenge(page):
                        self._logger.info("Challenge detected after login")
                        if self._verify_identity(page):
                            self._dismiss_popups(page)
                            return True
                except Exception:
                    continue

            if self._captcha_solver.available:
                self._logger.info("Retrying with captcha solve...")
                self._captcha_solver.detect_and_solve(page, timeout=120)
                if login_btn:
                    login_btn.click()
                else:
                    page.keyboard.press("Enter")
                for _ in range(30):
                    time.sleep(1)
                    if self._is_logged_in(page):
                        self._dismiss_popups(page)
                        return True

            if self._wait_for_manual_login():
                return True

            self._logger.warning("Auto-login failed")
            return False

        except Exception as e:
            self._logger.error("Auto-login failed: %s", e)
            return False

    def _save_session_cookies(self) -> None:
        if not self._user_data_dir:
            return
        try:
            cookies = self._browser.context.cookies()
            cookies_path = Path(self._user_data_dir) / "cookies_backup.json"
            with open(cookies_path, "w") as f:
                json.dump(cookies, f, indent=2)
            self._logger.info("Session cookies saved (%d) to %s", len(cookies), cookies_path)
        except Exception as e:
            self._logger.warning("Failed to save session cookies | error=%s", e)

    def _verify_identity(self, page: Page) -> bool:
        self._logger.info("Solving Meta verification...")

        confirm_selectors = [
            'button:has-text("It\'s Me")',
            'button:has-text("It was me")',
            'button:has-text("This was me")',
            'button:has-text("Confirm")',
            'button:has-text("Get Started")',
            'button:has-text("Continue")',
            'button:has-text("I Agree")',
            'a:has-text("Confirm")',
            'div[role="button"]:has-text("It\'s Me")',
            'div[role="button"]:has-text("Confirm")',
        ]
        for selector in confirm_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    self._logger.info("Clicked: %s", selector.split('"')[1] if '"' in selector else selector)
                    time.sleep(3)
                    if self._is_logged_in(page):
                        return True
            except Exception:
                continue

        if self._captcha_solver.available:
            self._captcha_solver.detect_and_solve(page, timeout=120)
            time.sleep(3)
            if self._is_logged_in(page):
                self._logger.info("Captcha solved, logged in")
                return True

        for selector in confirm_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    time.sleep(3)
                    if self._is_logged_in(page):
                        return True
            except Exception:
                continue

        submit_selectors = [
            'button[type="submit"]',
            'form button',
            'div[role="button"]:has-text("Submit")',
            'button:has-text("Send")',
        ]
        for selector in submit_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click()
                    time.sleep(3)
                    if self._is_logged_in(page):
                        self._logger.info("Submitted verification form")
                        return True
            except Exception:
                continue

        self._logger.info("Auto-verification did not resolve")
        return self._wait_for_manual_login()

    def _wait_for_manual_login(self) -> bool:
        self._logger.info("Waiting for manual login (up to 180s)...")
        last_page = None
        for i in range(180):
            time.sleep(1)
            try:
                pages = self._browser.context.pages
                if not pages:
                    continue
                last_page = pages[-1]
                if self._detect_challenge(last_page):
                    self._logger.info("Challenge detected, auto-verifying...")
                    self._verify_identity(last_page)
                if "login" not in last_page.url and "accounts" not in last_page.url:
                    if self._is_logged_in(last_page):
                        self._logger.info("Login detected after %ds", i + 1)
                        self._authenticated = True
                        return True
            except Exception:
                continue
        self._logger.error("Login timeout after 180s")
        if last_page:
            self._browser.screenshot(last_page, name="login_timeout")
        self._state = WatcherState.ERROR
        return False

    def poll(self) -> list[BaseEvent]:
        if not self._authenticated:
            self._logger.warning("Not authenticated, running authenticate()")
            if not self.authenticate():
                self._state = WatcherState.ERROR
                return []

        events: list[BaseEvent] = []

        try:
            dm_events = self._fetch_dms()
            events.extend(dm_events)

            notif_events = self._fetch_notifications()
            events.extend(notif_events)

        except Exception:
            self._logger.exception("Instagram poll failed")
            self._state = WatcherState.ERROR

        return events

    def _fetch_dms(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_dms = self._scrape_inbox()

        for dm in raw_dms:
            text = dm.get('text', '')
            event_id = f"ig_dm_{dm.get('thread_id', '')}_{hash(text) & 0xFFFFFFFF}"
            if self._seen.is_seen(event_id):
                continue

            sender = PlatformAccount(
                platform=self.platform,
                account_id=dm.get("sender_id", ""),
                display_name=dm.get("sender_username", "Unknown"),
                username=dm.get("sender_username", "unknown"),
            )

            content_type = ContentCategory.IMAGE if dm.get("has_image") else ContentCategory.TEXT

            content = ContentItem(
                content_id=dm.get("item_id", ""),
                platform=self.platform,
                content_type=content_type,
                text=dm.get("text", ""),
                media_urls=tuple(dm.get("media_urls", [])),
                created_at=(
                    datetime.fromtimestamp(dm["timestamp"])
                    if "timestamp" in dm else None
                ),
            )

            event = MessageEvent(
                event_id=event_id,
                platform=self.platform,
                timestamp=datetime.now(),
                sender=sender,
                content=content,
                conversation_id=dm.get("thread_id", ""),
                raw_data=dm,
            )
            events.append(event)
            self._seen.mark_seen(event_id)

        return events

    def _fetch_notifications(self) -> list[BaseEvent]:
        events: list[BaseEvent] = []
        raw_notifs = self._scrape_notifications()

        for notif in raw_notifs:
            event_id = f"ig_notif_{notif.get('id', '')}"
            if self._seen.is_seen(event_id):
                continue

            actor = PlatformAccount(
                platform=self.platform,
                account_id=notif.get("actor_id", ""),
                display_name=notif.get("actor_username", "Unknown"),
                username=notif.get("actor_username", "unknown"),
            )

            notif_type = notif.get("type", "")

            if notif_type == "comment":
                content = ContentItem(
                    content_id=notif.get("media_id", ""),
                    platform=self.platform,
                    content_type=ContentCategory.TEXT,
                    text=notif.get("text", ""),
                    url=f"https://instagram.com/p/{notif.get('media_code', '')}",
                )
                event: BaseEvent = CommentEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.now(),
                    author=actor,
                    content=content,
                    parent_post_id=notif.get("media_id", ""),
                    raw_data=notif,
                )
            elif notif_type == "mention":
                content = ContentItem(
                    content_id=notif.get("media_id", ""),
                    platform=self.platform,
                    content_type=ContentCategory.TEXT,
                    text=notif.get("text", ""),
                )
                event = MentionEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.now(),
                    mentioned_by=actor,
                    content=content,
                    source_url=f"https://instagram.com/p/{notif.get('media_code', '')}",
                    raw_data=notif,
                )
            elif notif_type == "like":
                event = LikeEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.now(),
                    actor=actor,
                    target_content_id=notif.get("media_id", ""),
                    raw_data=notif,
                )
            elif notif_type == "follow":
                event = FollowEvent(
                    event_id=event_id,
                    platform=self.platform,
                    timestamp=datetime.now(),
                    follower=actor,
                    followed_account_id=notif.get("target_id", ""),
                    raw_data=notif,
                )
            else:
                continue

            events.append(event)
            self._seen.mark_seen(event_id)

        return events

    def _is_logged_in(self, page: Page) -> bool:
        try:
            current_url = page.url
            if "login" in current_url or "accounts" in current_url:
                return False

            for sel in [
                'svg[aria-label="Home"]',
                'svg[aria-label="Profile"]',
                'a[href="/direct/inbox/"]',
                'div[role="navigation"]',
                'img[alt*="profile" i]',
                'div[data-pagelet="root"]',
            ]:
                try:
                    if page.query_selector(sel) and page.query_selector(sel).is_visible():
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def _wait_for_selector(
        self, page: Page, selector: str, timeout: int = 10,
    ) -> bool:
        try:
            page.wait_for_selector(selector, timeout=timeout * 1000)
            return True
        except Exception:
            return False

    def _scrape_inbox(self) -> list[dict]:
        if not self._authenticated:
            return []

        page = self._browser.new_page()
        captured_threads = []

        def on_api_response(response):
            if '/api/graphql' not in response.url:
                return
            try:
                body = response.json()
                inbox = body.get('data', {}).get('get_slide_mailbox_for_iris_subscription')
                if not inbox:
                    return
                for edge in inbox.get('threads_by_folder', {}).get('edges', []):
                    tid = edge.get('node', {}).get('as_ig_direct_thread', {}).get('thread_key', '')
                    if tid:
                        captured_threads.append(tid)
            except Exception:
                pass

        try:
            page.on('response', on_api_response)
            page.goto(f"{IG_BASE_URL}/direct/inbox/", timeout=30000, wait_until='networkidle')
            page.wait_for_timeout(6000)
            self._dismiss_popups(page)

            if not captured_threads:
                self._logger.info("No threads found")
                return []

            messages: list[dict] = []
            for tid in captured_threads[:self._config.max_events_per_poll]:
                try:
                    page.goto(f"{IG_BASE_URL}/direct/t/{tid}/", timeout=30000, wait_until='networkidle')
                    page.wait_for_timeout(3000)
                    self._dismiss_popups(page)

                    result = json.loads(page.evaluate("""
                        () => {
                            const header = document.querySelector('header h2, header span[dir="auto"], h2');
                            const name = header ? header.innerText.trim() : '';
                            const section = document.querySelector('section') || document.body;
                            const lines = section.innerText.split('\\n').map(l => l.trim()).filter(Boolean);
                            const skip = new Set(['Home','Reels','Search','Notifications','Create','Profile','More','Also from Meta','Your messages','Send message','Active','Message...','Send']);
                            let lastMsg = '';
                            for (let i = lines.length - 1; i >= 0; i--) {
                                const l = lines[i];
                                if (skip.has(l)) continue;
                                if (/^(Active|Seen|Delivered|Jul |Aug |Sep |Oct )/.test(l)) continue;
                                if (l !== name) { lastMsg = l; break; }
                            }
                            return JSON.stringify([name || lines[0] || 'unknown', lastMsg]);
                        }
                    """))
                    sender, msg_text = result[0], result[1]

                    msg_hash = hash(msg_text) & 0xFFFFFFFF
                    messages.append({
                        "thread_id": tid,
                        "item_id": f"{tid}_{msg_hash}",
                        "sender_id": "",
                        "sender_username": sender,
                        "text": msg_text,
                        "timestamp": int(time.time()),
                        "has_image": False,
                    })
                except Exception as e:
                    self._logger.warning("Failed to scrape thread %s: %s", tid, e)
                    continue

            self._logger.info("Found %d inbox threads", len(messages))
            return messages

        except Exception as e:
            self._logger.error("Failed to scrape inbox: %s", e)
            self._browser.screenshot(page, name="inbox_failed")
            return []
        finally:
            page.close()

    def _scrape_notifications(self) -> list[dict]:
        if not self._authenticated:
            return []

        page = self._browser.new_page()
        try:
            page.goto(f"{IG_BASE_URL}/notifications", timeout=30000)
            time.sleep(4)

            self._dismiss_popups(page)

            notifications: list[dict] = []
            notif_items = page.query_selector_all('div[role="button"]')

            for item in notif_items[:self._config.max_events_per_poll]:
                try:
                    txt = item.inner_text().strip()
                    if not txt:
                        continue

                    notif_id = f"notif_{hash(txt) & 0xFFFFFFFF}"

                    notif_type = "unknown"
                    if any(w in txt.lower() for w in ["liked", "like"]):
                        notif_type = "like"
                    elif "comment" in txt.lower():
                        notif_type = "comment"
                    elif any(w in txt.lower() for w in ["followed", "follows", "follow"]):
                        notif_type = "follow"
                    elif "mentioned" in txt.lower() or "@" in txt:
                        notif_type = "mention"

                    actor_name = "Instagram User"
                    user_match = re.search(r'^(\w[\w.]+)', txt)
                    if user_match:
                        actor_name = user_match.group(1)

                    notifications.append({
                        "id": notif_id,
                        "actor_id": "",
                        "actor_username": actor_name,
                        "type": notif_type,
                        "text": txt,
                        "media_id": "",
                        "media_code": "",
                        "target_id": "",
                    })
                except Exception:
                    continue

            self._logger.info("Scraped %d notifications", len(notifications))
            return notifications

        except Exception as e:
            self._logger.error("Failed to scrape notifications | error=%s", e)
            self._browser.screenshot(page, name="notifications_failed")
            return []
        finally:
            page.close()

    @staticmethod
    def _dismiss_popups(page: Page) -> None:
        for selector in [
            'button:has-text("Not Now")',
            'button:has-text("Save Info")',
            'button:has-text("Turn On")',
            'button:has-text("Cancel")',
            'svg[aria-label="Close"]',
            'div[role="dialog"] button:has-text("Close")',
            'button:has-text("Remind Me Later")',
            'button:has-text("Skip")',
            'button[class*="cancel"]',
        ]:
            try:
                for el in page.query_selector_all(selector):
                    if el.is_visible():
                        el.click()
                        time.sleep(0.5)
            except Exception:
                continue
