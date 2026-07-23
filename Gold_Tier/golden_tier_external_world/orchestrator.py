import sys, json, time, re, hashlib, uuid, shutil, logging
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8") if sys.platform == "win32" else None
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ["NODE_OPTIONS"] = "--no-deprecation"

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE / "golden_tier_external_world" / "session" / "instagram"
COOKIES_FILE = SESSION_DIR / "cookies_backup.json"
SEEN_FILE = BASE / "seen_messages.json"
IG_BASE_URL = "https://www.instagram.com"

_LOG = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)


_COLORS = {"cyan": "\033[96m", "green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m", "blue": "\033[94m", "reset": "\033[0m"}

def c(text, color):
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"

def log(msg, color=""):
    print(f"  {c('>', 'blue')} {c(msg, color)}")

def ok(msg):
    print(f"  {c('✓', 'green')} {c(msg, 'green')}")

def fail(msg):
    print(f"  {c('✗', 'red')} {c(msg, 'red')}")

import random

_acknowledgments = {
    "Security Issue": [
        "I take your security concern very seriously",
        "I understand security is a top priority for you",
        "Let me address your security concern right away",
        "Your account security matters to us",
    ],
    "Problem Report": [
        "I'm sorry you're dealing with this",
        "I understand how frustrating this must be",
        "Thank you for bringing this to my attention",
        "I appreciate you reporting this issue",
    ],
    "Support Request": [
        "I'm here to help you with this",
        "Let me assist you with your request",
        "I'll take care of this for you",
        "Consider it handled — let me work on this",
    ],
    "Question": [
        "That's a great question",
        "I'm happy to help clarify this for you",
        "That's a really good point",
        "Let me share what I know about this",
    ],
    "General Inquiry": [
        "Thank you for reaching out",
        "I appreciate your message",
        "Thanks for getting in touch",
        "I'm glad to hear from you",
    ],
}

_action_lines = {
    "Security Issue": [
        "I'm immediately investigating your account and will lock down any vulnerabilities",
        "I've flagged this as a priority and our security team is reviewing it now",
        "Let me walk through the security checklist and get back to you with fixes",
        "I'm resetting the affected credentials and monitoring for unusual activity",
    ],
    "Problem Report": [
        "I'm diagnosing the issue right now and will have a fix prepared shortly",
        "I've logged this in our tracking system and I'm working on the root cause",
        "Let me reproduce the problem on my end and find the best solution",
        "I'm prioritizing this fix — you should hear back from me soon",
    ],
    "Support Request": [
        "I'm looking into the best way to help you right now",
        "Let me gather the information you need and get back promptly",
        "I'm on it — I'll make sure you get the assistance you require",
        "I'm reviewing your situation and will provide a solution quickly",
    ],
    "Question": [
        "Here's what I can share based on my experience",
        "Let me break this down for you in detail",
        "I've looked into this and here's what I found",
        "Great question — here's everything you need to know",
    ],
    "General Inquiry": [
        "I've noted your message and I'll make sure everything is taken care of",
        "Let me review this and get back to you with all the details",
        "I'll personally make sure this gets handled properly",
        "I'm reviewing your message and will follow up soon",
    ],
}

_closings = [
    "I'll update you as soon as I have more information. Appreciate your patience!",
    "Let me know if you have any other questions in the meantime.",
    "I'll keep you posted every step of the way.",
    "Feel free to reach out if you need anything else before then.",
    "I'll make sure this is resolved smoothly for you.",
    "Thanks for trusting me with this — I won't let you down.",
]

def _extract_topics(text: str) -> list:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)
    stopwords = {"the", "and", "for", "are", "you", "not", "but", "all", "can",
                  "has", "had", "was", "its", "just", "been", "have", "with",
                  "this", "that", "from", "your", "will", "please", "need",
                  "help", "let", "know", "about", "what", "want", "hi", "hello"}
    topics = [w for w in words if w.lower() not in stopwords and len(w) > 2]
    seen = set()
    unique = []
    for t in topics:
        l = t.lower()
        if l not in seen:
            seen.add(l)
            unique.append(t)
    return unique[:5]

def _classify_intent(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in ["hack", "access", "password", "reset", "forgot", "blocked", "suspended"]):
        return "Security Issue"
    if any(kw in t for kw in ["problem", "issue", "broken", "error", "not working", "down", "critical"]):
        return "Problem Report"
    if any(kw in t for kw in ["urgent", "emergency", "need help", "help", "fix", "asap", "immediately"]):
        return "Support Request"
    if any(kw in t for kw in ["question", "tell me", "what is", "how to", "why", "price", "cost", "rate"]):
        return "Question"
    return "General Inquiry"

def _determine_priority(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in ["emergency", "critical", "urgent", "hack", "blocked", "suspended", "broken", "down", "immediately"]):
        return "HIGH"
    if any(kw in t for kw in ["problem", "issue", "need help", "help", "fix", "error", "not working", "asap"]):
        return "MEDIUM"
    return "LOW"

def _generate_plan_id():
    return "plan_" + uuid.uuid4().hex[:13]

def _now_ts():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now().microsecond:06d}Z"

def _update_seen_after_send(thread_id: str):
    if not SEEN_FILE.exists():
        return
    try:
        data = json.loads(SEEN_FILE.read_text())
        if isinstance(data, list):
            data = {"seen_keys": data, "last_activity": {}}
        data.setdefault("last_activity", {})
        data["last_activity"][thread_id] = str(int(time.time() * 1000))
        SEEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except:
        pass

def _now_filename():
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

def _parse_inbox_file(filepath: Path) -> dict:
    content = filepath.read_text(encoding="utf-8")
    data = {}
    in_frontmatter = False
    current_key = None
    for line in content.split("\n"):
        stripped = line.rstrip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            current_key = None
            continue
        if not in_frontmatter:
            break
        if not stripped:
            continue
        if stripped[0] == "-":
            if current_key:
                val = stripped.lstrip("- ").strip().strip('"')
                data.setdefault(current_key, []).append(val)
        elif stripped[0] != " " and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"')
            if val:
                data[key] = val
            else:
                current_key = key
        elif current_key and stripped.startswith("  "):
            if ":" in stripped:
                sub_key, _, sub_val = stripped.partition(":")
                sub_key = sub_key.strip()
                sub_val = sub_val.strip().strip('"')
                data[sub_key] = sub_val
    return data

def _extract_draft_from_pending(filepath: Path) -> Optional[str]:
    content = filepath.read_text(encoding="utf-8")
    marker = "## AI Draft Reply"
    if marker in content:
        parts = content.split(marker, 1)
        draft_section = parts[1].strip()
        draft_lines = []
        for line in draft_section.split("\n"):
            if line.startswith("## ") or line.startswith("---"):
                break
            draft_lines.append(line)
        return "\n".join(draft_lines).strip()
    return None

def _get_event_id_from_file(filepath: Path) -> str:
    data = _parse_inbox_file(filepath)
    return data.get("event_id", "unknown")

def _update_file_status(filepath: Path, new_status: str):
    content = filepath.read_text(encoding="utf-8")
    updated = re.sub(r'^status:.*$', f'status: {new_status}', content, flags=re.MULTILINE, count=1)
    filepath.write_text(updated, encoding="utf-8")

def _write_log(action: str, filename: str, details: str):
    logs_dir = BASE / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / "audit.md"
    ts = _now_ts()
    entry = f"| {ts} | {action} | {filename} | {details.replace('|', '/')} |"
    if logfile.exists():
        content = logfile.read_text(encoding="utf-8")
        if "|---|---|---|---|" not in content:
            content = "# Audit Log\n\n| Timestamp | Action | File | Details |\n|---|---|---|---|\n" + content
        logfile.write_text(content + "\n" + entry, encoding="utf-8")
    else:
        logfile.write_text(f"# Audit Log\n\n| Timestamp | Action | File | Details |\n|---|---|---|---|\n{entry}\n", encoding="utf-8")

def _update_dashboard():
    stats = {"pending": 0, "completed": 0}
    needs_action_dir = BASE / "Needs_Action"
    approved_dir = BASE / "Approved"
    pending_approval_dir = BASE / "Pending_Approval"
    done_dir = BASE / "Done"
    inbox_dir = BASE / "Inbox"

    for d in [needs_action_dir, pending_approval_dir, approved_dir, inbox_dir]:
        if d.exists():
            stats["pending"] += len([f for f in d.iterdir() if f.suffix == ".md"])
    if done_dir.exists():
        stats["completed"] = len([f for f in done_dir.iterdir() if f.suffix == ".md"])

    recent_activity = []
    logs_file = BASE / "Logs" / "audit.md"
    if logs_file.exists():
        lines = logs_file.read_text(encoding="utf-8").strip().split("\n")
        for line in reversed(lines):
            if line.startswith("| ") and "|---|---|---|---|" not in line and "Timestamp | Action | File | Details" not in line:
                parts = line.split("|")
                if len(parts) >= 4:
                    ts = parts[1].strip()
                    action = parts[2].strip()
                    fname = parts[3].strip()
                    details = parts[4].strip() if len(parts) > 4 else ""
                    recent_activity.append(f"- {ts} — **{action}** — {fname} {details}")
                if len(recent_activity) >= 5:
                    break

    last_updated = datetime.now().strftime("%Y-%m-%d %#I:%M %p")
    pending_md = ""
    for d_name, d_path in [("Inbox", inbox_dir), ("Needs_Action", needs_action_dir), ("Pending_Approval", pending_approval_dir), ("Approved", approved_dir)]:
        if d_path.exists():
            files = sorted(d_path.glob("*.md"))
            if files:
                pending_md += f"\n### {d_name}/\n"
                for f in files:
                    data = _parse_inbox_file(f)
                    msg = data.get("message", "?")
                    username = data.get("username", "?")
                    pending_md += f"- `{f.name}` — @{username}: \"{msg[:60]}{'...' if len(msg) > 60 else ''}\"\n"

    content = f"""# 📊 AI Employee Dashboard

---
last_updated: {last_updated}
status: active
tier: gold
---

## Overview
Your personal AI employee is running in Gold Tier mode.

## Quick Stats
| Metric | Value |
|--------|-------|
| Items Pending | {stats['pending']} |
| Items Completed | {stats['completed']} |
| Mode | Manual (Gold) |

## Pending Items
{pending_md if pending_md else '<!-- Check /Needs_Action/ folder for details -->'}

## Recent Activity
{chr(10).join(recent_activity) if recent_activity else '<!-- Check /Logs/ folder for full audit trail -->'}

---
*Generated by AI Employee v0.1 - Gold Tier*
"""
    dashboard = BASE / "Dashboard.md"
    dashboard.write_text(content.strip(), encoding="utf-8")
    ok("Dashboard updated")

def _generate_plan(source_file: Path, data: dict) -> Path:
    plans_dir = BASE / "Plan"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_id = _generate_plan_id()
    msg = data.get("message", "")
    intent = _classify_intent(msg)
    priority = _determine_priority(msg)
    ts = _now_ts()
    stem = source_file.stem.replace("Instagram-DM-", "Plan-Insta-DM-")
    plan_file = plans_dir / f"{stem}.md"

    content = f"""---
plan_id: {plan_id}
created_at: {ts}
source_file: {source_file.name}
event_id: {data.get('event_id', 'unknown')}
thread_id: {data.get('thread_id', 'unknown')}
from_username: "{data.get('username', 'unknown')}"
from_display: "{data.get('display_name', 'unknown')}"
message: "{msg}"
intent: {intent}
priority: {priority}
status: active
---

## Analysis
- **Intent Classified:** {intent}
- **Priority Level:** {priority}
- **Sender:** @{data.get('username', 'unknown')} ({data.get('display_name', 'unknown')})
- **Original Message:** "{msg}"

## Proposed Actions
1. Acknowledge receipt of the message
2. Analyze the request and prepare appropriate response
3. Draft reply based on intent classification
4. Send reply via Instagram DM
5. Follow up if needed

## Success Criteria
- Reply sent within the agreed SLA
- Sender receives a helpful and relevant response
- Issue resolved or appropriately escalated
"""
    plan_file.write_text(content.strip(), encoding="utf-8")
    return plan_file

def _generate_draft_reply(data: dict) -> str:
    msg = data.get("message", "")
    intent = _classify_intent(msg)
    priority = _determine_priority(msg)
    sender_name = data.get("display_name", data.get("username", "there"))

    topics = _extract_topics(msg)
    topic_phrase = ""
    if topics:
        topic_phrase = f" regarding {topics[0].lower()}" if len(topics) == 1 else \
                       f" regarding {', '.join(t.lower() for t in topics[:-1])} and {topics[-1].lower()}"

    ack = random.choice(_acknowledgments.get(intent, _acknowledgments["General Inquiry"]))
    action = random.choice(_action_lines.get(intent, _action_lines["General Inquiry"]))
    closing = random.choice(_closings)

    priority_prefix = {
        "HIGH": "I understand this is urgent, so I'm prioritizing it immediately.",
        "MEDIUM": "I'll make sure this gets handled promptly.",
        "LOW": "I'll take care of this as part of my regular workflow.",
    }.get(priority, "")

    greeting = f"Hi {sender_name}!"
    body = f"{ack}{topic_phrase}. {action}."

    if priority_prefix:
        body = f"{ack}{topic_phrase}. {priority_prefix} {action}."

    reply = f"{greeting}\n\n{body}\n\n{closing}"
    return reply

def process_inbox():
    inbox_dir = BASE / "Inbox"
    needs_action_dir = BASE / "Needs_Action"
    needs_action_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(inbox_dir.glob("*.md"))
    if not files:
        log("No files in Inbox", "yellow")
        return

    log(f"Processing {len(files)} file(s) from Inbox", "cyan")
    for f in files:
        data = _parse_inbox_file(f)
        msg = data.get("message", "")
        username = data.get("username", "unknown")
        log(f"  {f.name} — @{username}: \"{msg[:60]}{'...' if len(msg) > 60 else ''}\"")

        plan = _generate_plan(f, data)
        ok(f"Plan generated: {plan.name}")

        _update_file_status(f, "needs_action")
        dest = needs_action_dir / f.name
        shutil.move(str(f), str(dest))
        ok(f"Moved to Needs_Action/{f.name}")

        _write_log("In → Needs_Action", f.name, f"Plan: {plan.name} | From: @{username}")
        _update_dashboard()
        time.sleep(0.5)

def process_needs_action():
    needs_action_dir = BASE / "Needs_Action"
    pending_dir = BASE / "Pending_Approval"
    pending_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(needs_action_dir.glob("*.md"))
    if not files:
        log("No files in Needs_Action", "yellow")
        return

    log(f"Processing {len(files)} file(s) from Needs_Action", "cyan")
    for f in files:
        data = _parse_inbox_file(f)
        msg = data.get("message", "")
        username = data.get("username", "unknown")

        draft = _generate_draft_reply(data)
        log(f"  Draft generated for @{username}")

        _update_file_status(f, "pending_approval")
        content = f.read_text(encoding="utf-8")
        pending_content = content.strip() + f"""

## AI Draft Reply
{draft}

## Approval
<!-- User: review the draft above and run: python approve.py approve {f.name} -->
<!-- or: python approve.py reject {f.name} to send back to Needs_Action -->

Status: Pending Approval
"""
        pending_file = pending_dir / f.name
        pending_file.write_text(pending_content, encoding="utf-8")
        ok(f"Saved to Pending_Approval/{f.name}")

        f.unlink()
        ok(f"Removed from Needs_Action")

        _write_log("Needs_Action → Pending_Approval", f.name, f"Username: @{username}")
        _update_dashboard()
        time.sleep(0.5)

_shared_tab = {"pw": None, "page": None, "browser": None, "context": None}

def _ensure_tab(endpoint: str):
    alive = False
    if _shared_tab["page"]:
        try:
            _shared_tab["page"].evaluate("1")
            alive = True
        except Exception:
            pass
    if not alive:
        try:
            if _shared_tab["pw"]:
                _shared_tab["pw"].stop()
        except Exception:
            pass
        _shared_tab["pw"] = None
        _shared_tab["page"] = None
        _shared_tab["browser"] = None
        _shared_tab["context"] = None
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        _shared_tab["pw"] = pw
        _shared_tab["browser"] = browser
        _shared_tab["context"] = context
        _shared_tab["page"] = page

def _send_dm_reply(thread_id: str, draft_text: str) -> bool:
    endpoint_file = SESSION_DIR / "browser_endpoint.txt"
    if not endpoint_file.exists():
        fail("Agent browser not running — start agent_instagram.py first")
        return False

    endpoint = endpoint_file.read_text().strip()
    try:
        _ensure_tab(endpoint)
        page = _shared_tab["page"]

        log(f"Navigating to thread {thread_id}...")
        page.goto(f"{IG_BASE_URL}/direct/t/{thread_id}/", timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
        page.reload(wait_until="domcontentloaded")
        page.bring_to_front()
        time.sleep(4)

        _dismiss_popups(page)

        sent = _type_and_send(page, draft_text)
        if sent:
            ok("DM sent successfully")
            return True
        else:
            fail("Failed to send DM")
            _shared_tab["page"] = None
            return False
    except Exception as e:
        fail(f"Send error: {e}")
        _shared_tab["page"] = None
        return False

def _is_logged_in(page) -> bool:
    try:
        page.wait_for_selector('a[href*="direct"]', timeout=5000)
        return True
    except:
        pass
    try:
        page.wait_for_selector('svg[aria-label="Direct"]', timeout=3000)
        return True
    except:
        pass
    try:
        page.wait_for_selector('div[data-pagelet="root"]', timeout=3000)
        return True
    except:
        pass
    return False

def _dismiss_popups(page):
    for sel in [
        'div[role="button"]:has-text("Not Now")',
        'div[role="button"]:has-text("Save Info")',
        'button:has-text("Not Now")',
        'button:has-text("Remind Me Later")',
        'button:has-text("Skip")',
        'svg[aria-label="Close"]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(0.5)
        except:
            pass

def _type_and_send(page, text: str) -> bool:
    try:
        input_selectors = [
            'div[role="textbox"]',
            'textarea',
            'div[contenteditable="true"]',
        ]
        input_box = None
        for sel in input_selectors:
            try:
                input_box = page.query_selector(sel)
                if input_box:
                    break
            except:
                continue

        if not input_box:
            fail("Message input not found")
            return False

        input_box.click()
        time.sleep(0.5)
        page.keyboard.press("Control+A")
        time.sleep(0.2)
        page.keyboard.press("Delete")
        time.sleep(0.2)
        flat_text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        page.keyboard.type(flat_text, delay=60)
        time.sleep(1.5)

        typed = page.evaluate("""() => {
            const el = document.querySelector('div[role="textbox"], textarea, div[contenteditable="true"]');
            if (!el) return '';
            return el.innerText || el.textContent || el.value || '';
        }""")
        if not typed.strip():
            fail("Text was not typed into input box")
            return False
        log(f"Typed {len(typed.strip())} chars — verified in input", "green")

        page.keyboard.press("Enter")
        time.sleep(5)

        input_empty = page.evaluate("""() => {
            const el = document.querySelector('div[role="textbox"], textarea, div[contenteditable="true"]');
            if (!el) return false;
            const val = el.innerText || el.textContent || el.value || '';
            return val.trim().length === 0;
        }""")
        if not input_empty:
            log("Retrying Enter...", "yellow")
            page.keyboard.press("Enter")
            time.sleep(4)
            input_empty = page.evaluate("""() => {
                const el = document.querySelector('div[role="textbox"], textarea, div[contenteditable="true"]');
                if (!el) return false;
                const val = el.innerText || el.textContent || el.value || '';
                return val.trim().length === 0;
            }""")

        if not input_empty:
            fail("Input not cleared after retry — send failed")
            return False

        ok("DM sent successfully")
        return True
    except Exception as e:
        fail(f"Error typing/sending: {e}")
        return False

def process_approved():
    approved_dir = BASE / "Approved"
    done_dir = BASE / "Done"
    done_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(approved_dir.glob("*.md"))
    if not files:
        log("No files in Approved", "yellow")
        return

    log(f"Processing {len(files)} file(s) from Approved", "cyan")
    for f in files:
        data = _parse_inbox_file(f)
        thread_id = data.get("thread_id", "")
        if not thread_id:
            fail(f"No thread_id in {f.name} — cannot send")
            continue

        draft = _extract_draft_from_pending(f)
        if not draft:
            fail(f"No AI Draft Reply found in {f.name}")
            continue

        username = data.get("username", "unknown")
        log(f"Sending reply to @{username} (thread: {thread_id})")

        success = _send_dm_reply(thread_id, draft)
        if success:
            _update_file_status(f, "completed")
            _update_seen_after_send(thread_id)
            dest = done_dir / f.name
            shutil.move(str(f), str(dest))
            ok(f"Sent and moved to Done/{f.name}")
            _write_log("Approved → Done", f.name, f"Reply sent to @{username}")
        else:
            fail(f"Send failed for {f.name} — keeping in Approved")
            _write_log("Approved → FAILED", f.name, f"Send failed for @{username}")

        _update_dashboard()
        time.sleep(2)

def main():
    print(f"\n  {c('⚙', 'cyan')} {c('AI Employee Orchestrator (continuous)', 'cyan')}\n")

    log("Starting continuous pipeline (every 5s)...", "cyan")
    try:
        while True:
            try:
                process_inbox()
                process_needs_action()
                process_approved()

                _update_dashboard()
                time.sleep(5)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(f"Pipeline error: {e}", "red")
                time.sleep(5)
    except KeyboardInterrupt:
        log("\nStopped by user")
    finally:
        try:
            if _shared_tab.get("page"):
                _shared_tab["page"].close()
        except Exception:
            pass
        try:
            if _shared_tab.get("context"):
                _shared_tab["context"].close()
        except Exception:
            pass
        try:
            if _shared_tab.get("pw"):
                _shared_tab["pw"].stop()
        except Exception:
            pass
        _shared_tab["pw"] = None
        _shared_tab["page"] = None
        _shared_tab["browser"] = None
        _shared_tab["context"] = None

    print(f"\n  {c('✓', 'green')} {c('Orchestrator stopped', 'green')}\n")

if __name__ == "__main__":
    main()
