import sys, time, json, logging, os, re, hashlib
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8") if sys.platform == "win32" else None
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logging.getLogger("PlaywrightManager").setLevel(logging.WARNING)

os.environ["NODE_OPTIONS"] = "--no-deprecation"

from golden_tier_external_world.browser import PlaywrightManager
from golden_tier_external_world.config.enums import PlatformType, EventType as Et
from golden_tier_external_world.config.settings import WatcherConfig

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experimental"))
from instagram_like_watcher import check_likes
from instagram_mention_watcher import check_mentions

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "golden_tier_external_world" / "session" / "instagram"
COOKIES_FILE = SESSION_DIR / "cookies_backup.json"
INBOX_DIR = BASE_DIR / "Inbox"
SEEN_FILE = BASE_DIR / "seen_messages.json"
IG_BASE_URL = "https://www.instagram.com"

IMPORTANT_KEYWORDS = [
    "urgent", "emergency", "need help", "asap", "problem", "issue",
    "critical", "broken", "error", "fix", "help", "important",
    "immediately", "not working", "down", "blocked", "suspended",
    "hack", "access", "password", "reset", "forgot",
]

_LOG = logging.getLogger("agent")
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


def _has_session_id():
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text())
        return any(c.get("name") == "sessionid" for c in cookies)
    except Exception:
        return False


def _restore_cookies(browser):
    if not COOKIES_FILE.exists():
        return False
    try:
        cookies = json.loads(COOKIES_FILE.read_text())
        browser.context.add_cookies(cookies)
        log(f"Restored {len(cookies)} cookies")
        return True
    except Exception as e:
        log(f"Cookie restore failed: {e}", "yellow")
        return False


def _detect_challenge(page):
    if "challenge" in page.url:
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
    try:
        page.wait_for_selector('div[data-pagelet="root"]', timeout=3000)
        return True
    except Exception:
        pass
    return False


def _dismiss_popups(page):
    selectors = [
        'div[role="button"]:has-text("Not Now")',
        'div[role="button"]:has-text("Save Info")',
        'button:has-text("Not Now")',
        'button:has-text("Remind Me Later")',
        'button:has-text("Skip")',
        'button[class*="cancel"]',
        'div[role="dialog"] button:has-text("Close")',
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


def _extract_sender_and_message(page) -> tuple[dict, str]:
    result = json.loads(page.evaluate("""
        () => {
            const allLinks = Array.from(document.querySelectorAll('a[href]'));
            const profileLinks = [];
            const skipPaths = ['/direct/', '/accounts/', '/explore/', '/reels/', '/notifications/', '/search/', '/web/'];
            let ownerUsername = '';
            for (const a of allLinks) {
                const h = a.getAttribute('href');
                if (!h) continue;
                if (a.innerText.trim() === 'Profile' && /^\\/[a-zA-Z0-9_.]+\\/?$/.test(h)) {
                    ownerUsername = h.replace(/\\//g, '');
                }
                if (skipPaths.some(p => h.startsWith(p))) continue;
                if (/^\\/[a-zA-Z0-9_.]+\\/?$/.test(h)) {
                    const u = h.replace(/\\//g, '');
                    if (u && !profileLinks.includes(u)) profileLinks.push(u);
                }
            }
            const otherLinks = profileLinks.filter(u => u !== ownerUsername);
            const allH2 = Array.from(document.querySelectorAll('h2'));
            let displayName = '';
            for (let i = allH2.length - 1; i >= 0; i--) {
                const t = allH2[i].innerText.trim();
                if (t && t.length > 1 && !['Home','Reels','Search','Notifications','Create','Profile','More'].includes(t)) {
                    displayName = t; break;
                }
            }
            const section = document.querySelector('section') || document.body;
            const skipWords = new Set(['Home','Reels','Search','Notifications','Create','Profile','More',
                'Also from Meta','Your messages','Send message','Active','Message...','Send']);
            const msgDivs = Array.from(section.querySelectorAll('div')).filter(d => {
                const t = d.innerText.trim();
                if (!t || t.length < 2 || skipWords.has(t)) return false;
                if (/^(Active|Seen|Delivered|Jul |Aug |Sep |Oct |Nov |Dec |Jan |Feb |Mar)/.test(t)) return false;
                if (d.querySelector('input, textarea, [contenteditable]')) return false;
                if (d.offsetHeight < 20 || d.offsetWidth < 50) return false;
                return true;
            });
            let lastMsg = '';
            if (msgDivs.length > 0) {
                lastMsg = msgDivs[msgDivs.length - 1].innerText.trim();
            }
            const senderDivs = Array.from(document.querySelectorAll('div'));
            let username = '';
            const lastMsgIndex = section.innerText.indexOf(lastMsg);
            const before = section.innerText.substring(Math.max(0, lastMsgIndex - 200), lastMsgIndex);
            const senderNameLine = before.split('\\n').filter(Boolean).slice(-1)[0] || '';
            for (const div of senderDivs) {
                const t = div.innerText.trim();
                if (t === displayName || t === senderNameLine) {
                    const a = div.querySelector('a[href]');
                    if (a) {
                        const h = a.getAttribute('href');
                        const m = h.match(/^\\/([a-zA-Z0-9_.]+)\\/?$/);
                        if (m && m[1] !== ownerUsername) { username = m[1]; break; }
                    }
                    const next = div.nextElementSibling;
                    if (next) {
                        const nextText = next.innerText.trim();
                        if (nextText && nextText.length < 40 && !nextText.includes(' ') && otherLinks.includes(nextText)) {
                            username = nextText; break;
                        }
                    }
                }
            }
            if (!username) {
                const sorted = Array.from(document.querySelectorAll('div')).map(d => ({t: d.innerText.trim(), top: d.getBoundingClientRect().top})).filter(x => x.t && x.t.length > 2 && x.t.length < 40 && !x.t.includes(' ') && !skipWords.has(x.t) && !/^(Active|Seen|Delivered|Jul|Aug)/.test(x.t) && x.t !== ownerUsername).sort((a,b) => a.top - b.top);
                for (const item of sorted) {
                    if (otherLinks.includes(item.t)) { username = item.t; break; }
                }
            }
            if (!username && otherLinks.length > 0) {
                username = otherLinks[0];
            }
            let verified = false;
            const svgs = Array.from(document.querySelectorAll('svg'));
            for (const svg of svgs) {
                const lbl = svg.getAttribute('aria-label') || '';
                if (lbl.toLowerCase().includes('verified')) { verified = true; break; }
            }
            let platformId = 'unknown';
            const scripts = Array.from(document.querySelectorAll('script'));
            for (const sc of scripts) {
                const t = sc.innerText || '';
                const patterns = [/"pk"\\s*:\\s*"(\\d+)"/, /"pk"\\s*:\\s*(\\d+)/, /"user_id"\\s*:\\s*"(\\d+)"/];
                for (const pat of patterns) {
                    const m = t.match(pat);
                    if (m) { platformId = m[1]; break; }
                }
                if (platformId !== 'unknown') break;
            }
            return JSON.stringify({
                displayName: displayName || 'unknown',
                username: username || displayName.replace(/[^a-zA-Z0-9]/g,'').toLowerCase(),
                platformId: platformId,
                verified: verified,
                message: lastMsg
            });
        }
    """))
    return result, result.get("message", "")


def _has_important_keyword(text: str) -> bool:
    text_lower = text.lower()
    for kw in IMPORTANT_KEYWORDS:
        if kw in text_lower:
            return True
    return False


def _classify_intent(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in ["hack", "access", "password", "reset", "forgot", "blocked", "suspended"]):
        return "Security Issue"
    if any(kw in t for kw in ["problem", "issue", "broken", "error", "not working", "down", "critical"]):
        return "Problem Report"
    if any(kw in t for kw in ["urgent", "emergency", "need help", "help", "fix", "asap", "immediately"]):
        return "Support Request"
    if any(kw in t for kw in ["question", "tell me", "what is", "how to", "why"]):
        return "Question"
    return "General Inquiry"


def _determine_priority(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in ["emergency", "critical", "urgent", "hack", "blocked", "suspended", "broken", "down", "immediately"]):
        return "HIGH"
    if any(kw in t for kw in ["problem", "issue", "need help", "help", "fix", "error", "not working", "asap"]):
        return "MEDIUM"
    return "LOW"


def _load_seen():
    default = {"seen_keys": [], "last_activity": {}}
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            if isinstance(data, list):
                return {"seen_keys": data, "last_activity": {}}
            return {**default, **data}
        except Exception:
            pass
    return default


def _save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def _make_key(thread_id: str, text: str) -> str:
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
    return f"{thread_id}_{h}"


def _is_seen(thread_id: str, text: str, seen: dict) -> bool:
    return _make_key(thread_id, text) in seen.get("seen_keys", [])


def _mark_seen(thread_id: str, text: str, seen: dict):
    key = _make_key(thread_id, text)
    if key not in seen.setdefault("seen_keys", []):
        seen["seen_keys"].append(key)
        _save_seen(seen)


def _should_process(tid: str, last_ts: str, seen: dict) -> bool:
    prev = seen.get("last_activity", {}).get(tid, "0")
    last_ts = last_ts or "0"
    return last_ts > prev


def _update_last_activity(tid: str, last_ts: str, seen: dict):
    if last_ts:
        seen.setdefault("last_activity", {})[tid] = last_ts
        _save_seen(seen)


def _make_event_id():
    import uuid
    return "insta_msg_" + uuid.uuid4().hex[:13]


def _save_to_inbox(sender: dict, text: str, thread_id: str):
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    received_at = datetime.now().strftime("%Y-%m-%d %#I:%M %p")
    filename = f"Instagram-DM-{ts}.md"
    filepath = INBOX_DIR / filename
    priority = _determine_priority(text).lower()
    matched = [kw for kw in IMPORTANT_KEYWORDS if kw in text.lower()]
    clean_text = text.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ").replace("\r", "")
    content = f"""---
event_id: {_make_event_id()}
type: direct_message
platform: instagram

thread_id: \"{thread_id}\"

from:
  username: \"{sender['username']}\"
  display_name: \"{sender['displayName']}\"

message: \"{clean_text}\"

priority: {priority}

keywords:
{chr(10).join(f'  - {kw}' for kw in matched)}

received_at: \"{received_at}\"

status: pending
---"""
    filepath.write_text(content.strip(), encoding="utf-8")
    return filepath


def _get_unread_from_page(page, thread_info):
    page_text = page.evaluate("() => document.body.innerText")
    unread_tids = set()
    for tid, info in thread_info.items():
        display = info.get('sender', {}).get('displayName', '')
        username = info.get('sender', {}).get('username', '')
        name = display or username
        if not name:
            continue
        name_short = name[:25]
        if name_short in page_text:
            idx = page_text.index(name_short)
            chunk = page_text[idx:idx+400]
            if "Unread" in chunk:
                unread_tids.add(tid)
    return unread_tids


def main():
    if not _has_session_id():
        fail("No session found. Run watcher once to login first.")
        sys.exit(1)

    log("Launching browser...")
    browser = PlaywrightManager(
        user_data_dir=str(SESSION_DIR),
        headless=False,
        screenshot_dir=BASE_DIR / "vault" / "screenshots",
    )
    browser.start()
    page = browser.new_page()

    endpoint_file = SESSION_DIR / "browser_endpoint.txt"
    endpoint_file.write_text(f"http://127.0.0.1:9222")
    log("Browser endpoint saved", "green")

    try:
        _restore_cookies(browser)
        log("Navigating to Instagram...")
        page.goto(IG_BASE_URL, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        page.reload(wait_until="domcontentloaded")
        time.sleep(3)

        if _detect_challenge(page):
            log("Challenge detected — waiting for manual solve (60s)...", "yellow")
            time.sleep(60)

        if not _is_logged_in(page):
            log("Not logged in. Attempting login...", "yellow")
            page.goto(f"{IG_BASE_URL}/accounts/login/", timeout=30000)
            time.sleep(2)
            from golden_tier_external_world.config.secrets import get_secret
            username = get_secret("INSTAGRAM_EMAIL")
            password = get_secret("INSTAGRAM_PASSWORD")
            page.fill('input[name="username"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            time.sleep(5)
            if _detect_challenge(page):
                log("Challenge after login — waiting 60s...", "yellow")
                time.sleep(60)

        if not _is_logged_in(page):
            fail("Login failed")
            browser.stop()
            sys.exit(1)

        ok("Logged in")
        _dismiss_popups(page)
        time.sleep(2)

        def on_inbox_response(response):
            if '/api/graphql' not in response.url:
                return
            try:
                inbox = response.json().get('data', {}).get('get_slide_mailbox_for_iris_subscription')
                if not inbox:
                    return
                for edge in inbox.get('threads_by_folder', {}).get('edges', []):
                    node = edge.get('node', {}).get('as_ig_direct_thread', {})
                    tid = node.get('thread_key', '')
                    if not tid:
                        continue
                    last_ts = node.get('last_activity_timestamp_ms', '')
                    users = node.get('users', [])
                    sender = {}
                    for u in users:
                        uname = u.get('username', '')
                        if uname:
                            sender = {
                                'username': uname,
                                'displayName': u.get('full_name', '') or uname,
                                'platformId': str(u.get('pk', '')),
                                'verified': u.get('is_verified', False),
                            }
                            break
                    thread_info[tid] = {'last_ts': last_ts, 'sender': sender}
            except Exception:
                pass

        page.on('response', on_inbox_response)

        log("Starting continuous monitoring (every 5s)...", "cyan")
        thread_info = {}
        cycle = 0
        while True:
            try:
                cycle += 1
                thread_info.clear()
                seen = _load_seen()

                # DM check
                log(f"[{cycle}] Checking DMs...", "cyan")
                page.goto(f"{IG_BASE_URL}/direct/inbox/", timeout=30000, wait_until='domcontentloaded')
                time.sleep(5)
                _dismiss_popups(page)

                pending = {tid: info for tid, info in thread_info.items()
                           if _should_process(tid, info['last_ts'], seen)}

                if pending:
                    unread = _get_unread_from_page(page, thread_info)
                    pending = {tid: info for tid, info in pending.items() if tid in unread}

                if pending:
                    log(f"Found {len(pending)} unread thread(s)", "cyan")
                    for tid, info in list(pending.items()):
                        try:
                            log(f"Opening thread {tid}...")
                            page.goto(f"{IG_BASE_URL}/direct/t/{tid}/", timeout=30000, wait_until="networkidle")
                            time.sleep(3)
                            _dismiss_popups(page)

                            sender_raw, last_msg = _extract_sender_and_message(page)
                            api_user = info.get('sender', {})
                            sender = {
                                "username": api_user.get("username", sender_raw.get("username", "unknown")),
                                "displayName": api_user.get("displayName", sender_raw.get("displayName", "unknown")),
                                "platformId": api_user.get("platformId", sender_raw.get("platformId", "unknown")),
                                "verified": api_user.get("verified", sender_raw.get("verified", False)),
                            }
                            log(f"From: @{sender['username']} ({sender['displayName']})", "cyan")
                            log(f"Msg: {repr(last_msg[:120])}", "cyan")

                            if not last_msg:
                                log("No message found — skipping", "yellow")
                                continue

                            if _is_seen(tid, last_msg, seen):
                                log("Already processed — skipping", "yellow")
                                continue

                            if _has_important_keyword(last_msg):
                                filepath = _save_to_inbox(sender, last_msg, tid)
                                _mark_seen(tid, last_msg, seen)
                                ok(f"Important → saved to Inbox/{filepath.name}")
                            else:
                                log("Not important — skipped", "yellow")

                            _update_last_activity(tid, info.get('last_ts', ''), seen)
                            time.sleep(1)
                        except Exception as e:
                            log(f"Error: {e}", "red")
                            continue
                else:
                    log("No unread DMs", "yellow")

                # Like check every 3 cycles
                if cycle % 3 == 0:
                    log(f"[{cycle}] Checking likes...", "cyan")
                    try:
                        check_likes(page)
                    except Exception as e:
                        log(f"Like check error: {e}", "yellow")

                # Mention check every cycle (HIGH priority)
                if cycle % 1 == 0:
                    try:
                        check_mentions(page)
                    except Exception as e:
                        log(f"Mention check error: {e}", "yellow")

                time.sleep(5)

            except KeyboardInterrupt:
                log("\nStopped by user")
                break
            except Exception as e:
                log(f"Check error: {e}", "yellow")
                time.sleep(5)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        fail(f"Error: {e}")
    finally:
        if endpoint_file.exists():
            endpoint_file.unlink()
            log("Browser endpoint removed", "green")
        try:
            browser.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
