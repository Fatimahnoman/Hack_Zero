"""
Instagram Like Watcher — Detects new likes on your posts, deduplicates, and logs events.
No replies or AI responses — purely observational.

Standalone:  python instagram_like_watcher.py --once
Integrated:   from instagram_like_watcher import check_likes
"""

import sys
import os
import json
import time
import hashlib
import random
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
LIKE_DIR = BASE_DIR / "Likes"
SEEN_FILE = EVENTS_DIR / "like_seen.json"
IG_BASE_URL = "https://www.instagram.com"

EVENTS_DIR.mkdir(parents=True, exist_ok=True)

_LOG = logging.getLogger("like_watcher")
_COLORS = {
    "cyan": "\033[96m", "green": "\033[92m", "yellow": "\033[93m",
    "red": "\033[91m", "blue": "\033[94m", "reset": "\033[0m",
}


def c(text, color):
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def log(msg, color=""):
    print(f"  {c('>', 'blue')} {c(msg, color)}")


def ok(msg):
    print(f"  {c('✓', 'green')} {c(msg, 'green')}")


def fail(msg):
    print(f"  {c('✗', 'red')} {c(msg, 'red')}")


# --- Seen / Deduplication ---
def _load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen_keys": []}


def _save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def _make_key(username: str, post_url: str) -> str:
    # Normalize URL: strip /liked_by/, trailing slashes, query params
    normalized = post_url.split("?")[0].rstrip("/")
    for suffix in ["/liked_by", "/liked_by/"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    raw = f"{username}_{normalized}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _is_seen(username: str, post_url: str, seen: dict) -> bool:
    return _make_key(username, post_url) in seen.get("seen_keys", [])


def _mark_seen(username: str, post_url: str, seen: dict):
    key = _make_key(username, post_url)
    keys = seen.setdefault("seen_keys", [])
    if key not in keys:
        keys.append(key)
        # Keep only last 2000 keys to prevent file bloat
        if len(keys) > 2000:
            seen["seen_keys"] = keys[-2000:]
        _save_seen(seen)


# --- Event Logging ---
def _log_event(username: str, display_name: str, post_url: str, like_count: int = 0) -> Path:
    import uuid
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    received = datetime.now().strftime("%Y-%m-%d %#I:%M %p")
    event_id = uuid.uuid4().hex[:13]
    filename = f"Instagram-Like-{event_id}.md"

    LIKE_DIR.mkdir(parents=True, exist_ok=True)
    filepath = LIKE_DIR / filename

    others_text = ""
    if like_count > 0:
        others_text = f"\nothers_liked: {like_count}"

    content = f"""---
# Instagram Like Event

type: LIKE

platform: instagram

from: @{username}

display_name: {display_name}

post_url: "{post_url}"{others_text}

priority: low

AI Decision: Ignore

Reason: Like events do not require a response.

received: {received}

status: logged
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


def has_dm_badge(page) -> bool:
    """Check if DM icon has a notification badge (unread messages)."""
    try:
        result = page.evaluate(r"""() => {
            // Find DM/Messages SVG icon
            const svgs = document.querySelectorAll('svg[aria-label="Messages"], svg[aria-label="Direct"]');
            for (const svg of svgs) {
                // Go up to find the parent link or container
                let container = svg.closest('a') || svg.parentElement;
                for (let i = 0; i < 5 && container; i++) {
                    const spans = container.querySelectorAll('span');
                    for (const span of spans) {
                        const t = span.innerText.trim();
                        if (t && !isNaN(parseInt(t)) && parseInt(t) > 0) return true;
                        // Check for colored dot badge
                        const style = window.getComputedStyle(span);
                        const rect = span.getBoundingClientRect();
                        if (rect.width > 0 && rect.width < 25 && rect.height > 0 && rect.height < 25 &&
                            style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
                            style.backgroundColor !== 'transparent' &&
                            style.backgroundColor !== 'rgb(255, 255, 255)') {
                            return true;
                        }
                    }
                    container = container.parentElement;
                }
            }
            return false;
        }""")
        return result
    except Exception:
        return False


def has_notification_badge(page) -> bool:
    """Check if notification (heart) icon has a badge (new activity)."""
    try:
        result = page.evaluate(r"""() => {
            const svgs = document.querySelectorAll('svg[aria-label="Notifications"], svg[aria-label="Heart"], svg[aria-label="Notification"]');
            for (const svg of svgs) {
                let container = svg.closest('a') || svg.parentElement;
                for (let i = 0; i < 5 && container; i++) {
                    const spans = container.querySelectorAll('span');
                    for (const span of spans) {
                        const t = span.innerText.trim();
                        if (t && !isNaN(parseInt(t)) && parseInt(t) > 0) return true;
                        const style = window.getComputedStyle(span);
                        const rect = span.getBoundingClientRect();
                        if (rect.width > 0 && rect.width < 25 && rect.height > 0 && rect.height < 25 &&
                            style.backgroundColor !== 'rgba(0, 0, 0, 0)' &&
                            style.backgroundColor !== 'transparent' &&
                            style.backgroundColor !== 'rgb(255, 255, 255)') {
                            return true;
                        }
                    }
                    container = container.parentElement;
                }
            }
            return false;
        }""")
        return result
    except Exception:
        return False


# --- Like Detection ---
def _extract_likes_from_notifications(page) -> list[dict]:
    likes = []
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
                if (!text.match(/liked your (post|reel|photo|video|story)/i)) continue;

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

                const beforeLiked = fullText.replace(/liked your.*/i, '').trim();
                const cleanBefore = beforeLiked.replace(/and \d+ other[s]?/gi, '').replace(/,\s*$/, '').trim();
                const usernames = cleanBefore.split(/[,&]\s*/).map(s => s.trim()).filter(s => s.length > 0);
                const displayName = usernames[0] || '';
                const username = displayName.toLowerCase().replace(/\s+/g, '');

                // Extract like count from "X and Y others liked" or "and X others liked"
                let likeCount = 0;
                const othersMatch = fullText.match(/and (\d+) other[s]? liked/i);
                if (othersMatch) {
                    likeCount = parseInt(othersMatch[1]);
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
                        postUrl: postUrl || 'unknown',
                        likeCount: likeCount,
                    });
                }
            }
            return results;
        }""")

        if raw:
            likes = raw

    except Exception as e:
        log(f"Notification fetch error: {e}", "yellow")

    return likes


def _check_activity_tab(page) -> list[dict]:
    likes = []
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
                if (!text.match(/liked your (post|reel|photo|video|story)/i)) continue;

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

                const beforeLiked = fullText.replace(/liked your.*/i, '').trim();
                const cleanBefore = beforeLiked.replace(/and \d+ other[s]?/gi, '').replace(/,\s*$/, '').trim();
                const usernames = cleanBefore.split(/[,&]\s*/).map(s => s.trim()).filter(s => s.length > 0);
                const displayName = usernames[0] || '';
                const username = displayName.toLowerCase().replace(/\s+/g, '');

                let likeCount = 0;
                const othersMatch = fullText.match(/and (\d+) other[s]? liked/i);
                if (othersMatch) {
                    likeCount = parseInt(othersMatch[1]);
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
                        postUrl: postUrl || 'unknown',
                        likeCount: likeCount,
                    });
                }
            }
            return results;
        }""")

        if raw:
            likes = raw

        page.keyboard.press("Escape")
        time.sleep(0.5)

    except Exception as e:
        log(f"Activity tab error: {e}", "yellow")

    return likes


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


def check_likes(page) -> int:
    """
    Check Instagram notifications for new likes.
    Uses the same browser page as the DM watcher.
    Returns number of new likes found.
    """
    seen = _load_seen()
    new_count = 0

    for attempt in range(3):
        try:
            likes = _extract_likes_from_notifications(page)
            if not likes:
                likes = _check_activity_tab(page)

            for like in likes:
                if not _is_seen(like["username"], like["postUrl"], seen):
                    _mark_seen(like["username"], like["postUrl"], seen)
                    filepath = _log_event(
                        like["username"],
                        like["displayName"],
                        like["postUrl"],
                        like.get("likeCount", 0),
                    )
                    log(f"  NEW LIKE: @{like['username']} → {filepath.name}", "green")
                    new_count += 1

            if new_count > 0:
                ok(f"{new_count} new like(s) logged")
            else:
                log(f"  No new likes ({len(likes)} total seen)", "yellow")

            # Go back to home feed after check
            try:
                page.goto(IG_BASE_URL, timeout=20000, wait_until="domcontentloaded")
                time.sleep(2)
            except Exception:
                pass

            break

        except Exception as e:
            log(f"  Like check attempt {attempt+1} failed: {e}", "yellow")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                recovered = _recover_page(page)
                if recovered:
                    log("  Page recovered, retrying...", "cyan")
                else:
                    log("  Page recovery failed, retrying anyway...", "yellow")
            else:
                log(f"  Like check giving up after 3 attempts", "red")

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
            check_likes(page)
            return

        log("Starting like watcher (every 30-60s)...", "cyan")
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
                check_likes(page)

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
    parser = argparse.ArgumentParser(description="Instagram Like Watcher")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    args = parser.parse_args()
    start_watcher(once=args.once)
