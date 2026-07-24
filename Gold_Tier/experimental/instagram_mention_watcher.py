"""
Instagram Mention Watcher — Detects new mentions on your posts, deduplicates, and logs events.
Saves to Inbox/ for AI-powered reply generation.

Standalone:  python instagram_mention_watcher.py --once
Integrated:   from instagram_mention_watcher import check_mentions
"""

import sys
import os
import json
import time
import hashlib
import random
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8") if sys.platform == "win32" else None
os.environ["NODE_OPTIONS"] = "--no-deprecation"

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logging.getLogger("PlaywrightManager").setLevel(logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golden_tier_external_world.browser import PlaywrightManager

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "golden_tier_external_world" / "session" / "instagram"
EVENTS_DIR = BASE_DIR / "experimental" / "logs"
INBOX_DIR = BASE_DIR / "Inbox"
SEEN_FILE = EVENTS_DIR / "mention_seen.json"
IG_BASE_URL = "https://www.instagram.com"

EVENTS_DIR.mkdir(parents=True, exist_ok=True)

_LOG = logging.getLogger("mention_watcher")
_COLORS = {
    "cyan": "\033[96m", "green": "\033[92m", "yellow": "\033[93m",
    "red": "\033[91m", "blue": "\033[94m", "reset": "\033[0m",
}


def c(text, color):
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def log(msg, color=""):
    print(f"  {c('>', 'blue')} {c(msg, color)}")


def ok(msg):
    print(f"  {c('+', 'green')} {c(msg, 'green')}")


def fail(msg):
    print(f"  {c('x', 'red')} {c(msg, 'red')}")


# --- Seen / Deduplication ---
def _load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {"seen_keys": data}
            return data
        except Exception:
            pass
    return {"seen_keys": []}


def _save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def _make_key(username: str, post_url: str) -> str:
    normalized = post_url.split("?")[0].rstrip("/")
    raw = f"mention_{username}_{normalized}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _make_user_key(username: str) -> str:
    return f"user_{username.lower()}"


def _is_seen(username: str, post_url: str, seen: dict) -> bool:
    key = _make_key(username, post_url)
    user_key = _make_user_key(username)
    seen_keys = seen.get("seen_keys", [])
    if key in seen_keys or user_key in seen_keys:
        return True
    if _inbox_has_pending(username):
        return True
    return False


def _mark_seen(username: str, post_url: str, seen: dict):
    keys = seen.setdefault("seen_keys", [])
    key = _make_key(username, post_url)
    user_key = _make_user_key(username)
    changed = False
    if key not in keys:
        keys.append(key)
        changed = True
    if user_key not in keys:
        keys.append(user_key)
        changed = True
    if changed:
        if len(keys) > 2000:
            seen["seen_keys"] = keys[-2000:]
        _save_seen(seen)


def _inbox_has_pending(username: str) -> bool:
    if not INBOX_DIR.exists():
        return False
    username_lower = username.lower()
    for f in INBOX_DIR.glob("Instagram-Mention-*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            if f"mentioned_by: {username_lower}" in content.lower() and "status: pending" in content.lower():
                return True
        except Exception:
            continue
    return False


# --- Event Logging ---
def _log_event(username: str, caption: str, post_url: str) -> Path:
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    received = datetime.now().strftime("%Y-%m-%d %#I:%M %p")
    event_id = uuid.uuid4().hex[:13]
    filename = f"Instagram-Mention-{event_id}.md"

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    filepath = INBOX_DIR / filename

    content = f"""---
# Instagram Mention Event

type: MENTION

platform: instagram

mentioned_by: {username}

caption: {caption}

post_url: "{post_url}"

priority: HIGH

AI Decision: Generate Thank You Reply

Reason: Someone mentioned you in their post/comment. Generate a polite thank you reply.

received: {received}

status: pending
---"""

    filepath.write_text(content.strip(), encoding="utf-8")
    return filepath


# --- Browser Helpers ---
def _dismiss_popups(page):
    selectors = [
        'div[role="button"]:has-text("Not Now")',
        'div[role="button"]:has-text("Save Info")',
        'button:has-text("Not Now")',
        'button:has-text("Remind Me Later")',
        'button:has-text("Skip")',
        'svg[aria-label="Close"]',
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(0.5)
        except Exception:
            continue


# --- Mention Detection ---
def _extract_mentions_from_notifications(page) -> list[dict]:
    mentions = []
    try:
        page.goto(f"{IG_BASE_URL}/accounts/activity/", timeout=20000,
                   wait_until="domcontentloaded")
        time.sleep(5)

        if "login" in page.url.lower():
            log("Session expired — redirected to login page", "red")
            return []

        _dismiss_popups(page)
        time.sleep(2)

        raw = page.evaluate(r"""() => {
            const results = [];
            const seen = new Set();

            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                null,
                false
            );

            while (walker.nextNode()) {
                const text = walker.currentNode.textContent.trim();
                if (!text) continue;
                if (!text.match(/mentioned you in (a |their )?(comment|post|reel|photo|video|story)/i)) continue;

                let container = walker.currentNode.parentElement;
                for (let i = 0; i < 10 && container; i++) {
                    if (container.innerText && container.innerText.length > 20 && container.innerText.length < 500) break;
                    container = container.parentElement;
                }
                if (!container) continue;

                const fullText = container.innerText.trim();

                const key = fullText.substring(0, 100);
                if (seen.has(key)) continue;
                seen.add(key);

                // Extract username: text before "mentioned you in"
                const beforeMentioned = fullText.replace(/mentioned you in.*/i, '').trim();
                const cleanBefore = beforeMentioned.replace(/and \d+ other[s]?/gi, '').replace(/,\s*$/, '').trim();
                // Take only the first word/username (stop at space, comma, or special chars)
                const usernameMatch = cleanBefore.match(/^([a-zA-Z0-9._]+)/);
                const displayName = usernameMatch ? usernameMatch[1] : '';
                const username = displayName.toLowerCase().replace(/\s+/g, '');

                // Extract caption text: text after "mentioned you in..." pattern
                let caption = '';
                const captionMatch = fullText.match(/mentioned you in (?:a |their )?(?:comment|post|reel|photo|video|story)[\s:]*(.+)/i);
                if (captionMatch) {
                    caption = captionMatch[1].trim().substring(0, 200);
                }
                if (!caption) {
                    caption = fullText.substring(0, 200);
                }

                // Extract post link
                let postUrl = '';
                let searchEl = container;
                for (let i = 0; i < 10 && searchEl; i++) {
                    const links = searchEl.querySelectorAll('a[href]');
                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        if (href.includes('/p/') || href.includes('/reel/') || href.includes('/tv/')) {
                            postUrl = 'https://www.instagram.com' + href;
                            break;
                        }
                    }
                    if (postUrl) break;
                    if (searchEl.tagName === 'A') {
                        const href = searchEl.getAttribute('href') || '';
                        if (href.includes('/p/') || href.includes('/reel/')) {
                            postUrl = 'https://www.instagram.com' + href;
                            break;
                        }
                    }
                    searchEl = searchEl.parentElement;
                }

                if (username) {
                    results.push({
                        username: username,
                        displayName: displayName,
                        caption: caption,
                        postUrl: postUrl || 'unknown',
                    });
                }
            }
            return results;
        }""")

        if raw:
            mentions = raw

    except Exception as e:
        log(f"Notification fetch error: {e}", "yellow")

    return mentions


def _check_activity_tab(page) -> list[dict]:
    mentions = []
    try:
        heart = page.query_selector('svg[aria-label="Notifications"]') or \
                page.query_selector('a[href="/accounts/activity/"]')
        if heart:
            heart.click()
            time.sleep(3)

        raw = page.evaluate(r"""() => {
            const results = [];
            const seen = new Set();

            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                null,
                false
            );

            while (walker.nextNode()) {
                const text = walker.currentNode.textContent.trim();
                if (!text) continue;
                if (!text.match(/mentioned you in (a |their )?(comment|post|reel|photo|video|story)/i)) continue;

                let container = walker.currentNode.parentElement;
                for (let i = 0; i < 10 && container; i++) {
                    if (container.innerText && container.innerText.length > 20 && container.innerText.length < 500) break;
                    container = container.parentElement;
                }
                if (!container) continue;

                const fullText = container.innerText.trim();
                const key = fullText.substring(0, 100);
                if (seen.has(key)) continue;
                seen.add(key);

                const beforeMentioned = fullText.replace(/mentioned you in.*/i, '').trim();
                const cleanBefore = beforeMentioned.replace(/and \d+ other[s]?/gi, '').replace(/,\s*$/, '').trim();
                const usernameMatch = cleanBefore.match(/^([a-zA-Z0-9._]+)/);
                const displayName = usernameMatch ? usernameMatch[1] : '';
                const username = displayName.toLowerCase().replace(/\s+/g, '');

                let caption = '';
                const captionMatch = fullText.match(/mentioned you in (?:a |their )?(?:comment|post|reel|photo|video|story)[\s:]*(.+)/i);
                if (captionMatch) {
                    caption = captionMatch[1].trim().substring(0, 200);
                }
                if (!caption) {
                    caption = fullText.substring(0, 200);
                }

                let postUrl = '';
                let searchEl = container;
                for (let i = 0; i < 10 && searchEl; i++) {
                    const links = searchEl.querySelectorAll('a[href]');
                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        if (href.includes('/p/') || href.includes('/reel/') || href.includes('/tv/')) {
                            postUrl = 'https://www.instagram.com' + href;
                            break;
                        }
                    }
                    if (postUrl) break;
                    if (searchEl.tagName === 'A') {
                        const href = searchEl.getAttribute('href') || '';
                        if (href.includes('/p/') || href.includes('/reel/')) {
                            postUrl = 'https://www.instagram.com' + href;
                            break;
                        }
                    }
                    searchEl = searchEl.parentElement;
                }

                if (username) {
                    results.push({
                        username: username,
                        displayName: displayName,
                        caption: caption,
                        postUrl: postUrl || 'unknown',
                    });
                }
            }
            return results;
        }""")

        if raw:
            mentions = raw

        page.keyboard.press("Escape")
        time.sleep(0.5)

    except Exception as e:
        log(f"Activity tab error: {e}", "yellow")

    return mentions


# --- Public API (called by agent_instagram.py) ---
def _recover_page(page) -> bool:
    """Try to recover the page if it's in a bad state."""
    try:
        current = page.url
        if "instagram.com" in current:
            page.reload(wait_until="domcontentloaded")
            time.sleep(3)
            return True
        else:
            page.goto(IG_BASE_URL, timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            return True
    except Exception:
        return False


def check_mentions(page) -> int:
    """
    Check Instagram notifications for new mentions.
    Uses the same browser page as the DM/like watcher.
    Returns number of new mentions found.
    """
    seen = _load_seen()
    new_count = 0

    for attempt in range(3):
        try:
            mentions = _extract_mentions_from_notifications(page)
            if not mentions:
                mentions = _check_activity_tab(page)

            for mention in mentions:
                if not _is_seen(mention["username"], mention["postUrl"], seen):
                    _mark_seen(mention["username"], mention["postUrl"], seen)
                    filepath = _log_event(
                        mention["username"],
                        mention["caption"],
                        mention["postUrl"],
                    )
                    log(f"  NEW MENTION: @{mention['username']} -> {filepath.name}", "green")
                    new_count += 1

            if new_count > 0:
                ok(f"{new_count} new mention(s) logged to Inbox/")
            else:
                log(f"  No new mentions ({len(mentions)} total seen)", "yellow")

            # Go back to home feed after check
            try:
                page.goto(IG_BASE_URL, timeout=20000, wait_until="domcontentloaded")
                time.sleep(2)
            except Exception:
                pass

            break

        except Exception as e:
            log(f"  Mention check attempt {attempt+1} failed: {e}", "yellow")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                recovered = _recover_page(page)
                if recovered:
                    log("  Page recovered, retrying...", "cyan")
                else:
                    log("  Page recovery failed, retrying anyway...", "yellow")
            else:
                log(f"  Mention check giving up after 3 attempts", "red")

    return new_count


# --- Cookie Restore ---
COOKIES_FILE = SESSION_DIR / "cookies_backup.json"

def _restore_cookies(pw_manager):
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        pw_manager.context.add_cookies(cookies)
        log(f"Restored {len(cookies)} cookies")
        return True
    except Exception as e:
        log(f"Cookie restore failed: {e}", "yellow")
        return False


def _is_logged_in(page):
    try:
        page.wait_for_selector('a[href*="direct"]', timeout=5000)
        return True
    except Exception:
        pass
    try:
        page.wait_for_selector('svg[aria-label="Direct"]', timeout=3000)
        return True
    except Exception:
        pass
    return False


# --- Standalone Mode ---
def start_watcher(once=False):
    seen = _load_seen()

    with PlaywrightManager(
        user_data_dir=str(SESSION_DIR),
        headless=False,
    ) as pw:
        page = pw.new_page()

        _restore_cookies(pw)

        log("Navigating to Instagram...")
        page.goto(IG_BASE_URL, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        page.reload(wait_until="domcontentloaded")
        time.sleep(3)

        if not _is_logged_in(page):
            log("Not logged in — please log in manually in the browser window", "yellow")
            log("Waiting 60s for login...", "yellow")
            time.sleep(60)
            page.reload(wait_until="domcontentloaded")
            time.sleep(3)

        _dismiss_popups(page)

        if _is_logged_in(page):
            try:
                cookies = pw.context.cookies()
                COOKIES_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                log(f"Saved {len(cookies)} cookies", "green")
            except Exception:
                pass

        ok("Ready")

        if once:
            log("Single check mode...", "cyan")
            check_mentions(page)
            return

        log("Starting mention watcher (every 30-60s)...", "cyan")
        cycle = 0
        while True:
            try:
                cycle += 1
                now = datetime.now().strftime("%H:%M:%S")
                log(f"[{cycle}] Checking ({now})...")

                if "instagram.com" not in page.url:
                    page.goto(IG_BASE_URL, timeout=30000, wait_until="domcontentloaded")
                    time.sleep(3)

                _dismiss_popups(page)
                check_mentions(page)

                wait = random.randint(30, 60)
                log(f"  Sleeping {wait}s...")
                time.sleep(wait)

            except KeyboardInterrupt:
                log("\nStopped by user")
                break
            except Exception as e:
                log(f"Error: {e}", "red")
                time.sleep(10)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Instagram Mention Watcher")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    args = parser.parse_args()
    start_watcher(once=args.once)
