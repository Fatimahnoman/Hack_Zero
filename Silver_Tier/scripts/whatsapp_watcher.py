"""
WhatsApp Watcher - Silver Tier (Simple Detection Only)
======================================================
Only monitors WhatsApp Web for unread messages with keywords.
Creates files in Inbox/ folder.
Orchestrator handles: Needs_Action → Plans → Pending_Approval → Approved → Done

Run: python scripts/whatsapp_watcher.py
"""

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] Install Playwright: pip install playwright && playwright install chromium")
    sys.exit(1)

# Configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = PROJECT_ROOT / "session" / "whatsapp"
INBOX_FOLDER = PROJECT_ROOT / "Inbox"
NEEDS_ACTION_FOLDER = PROJECT_ROOT / "Needs_Action"
LOGS_FOLDER = PROJECT_ROOT / "Logs"

# Ensure folders exist
for folder in [SESSION_PATH, INBOX_FOLDER, NEEDS_ACTION_FOLDER, LOGS_FOLDER]:
    folder.mkdir(parents=True, exist_ok=True)

WHATSAPP_URL = "https://web.whatsapp.com"
CHECK_INTERVAL = 15  # seconds

# Keywords to monitor
IMPORTANT_KEYWORDS = ["urgent", "asap", "invoice", "payment", "help"]

# Track processed messages
processed_messages = set()
PROCESSED_FILE = LOGS_FOLDER / "whatsapp_processed.ids"

def load_processed():
    global processed_messages
    if PROCESSED_FILE.exists():
        try:
            processed_messages = set(PROCESSED_FILE.read_text().splitlines())
        except:
            processed_messages = set()

def save_processed(msg_id: str):
    processed_messages.add(msg_id)
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(f"{msg_id}\n")

def get_priority(message: str) -> str:
    text = message.lower()
    if "urgent" in text or "asap" in text:
        return "high"
    elif "invoice" in text or "payment" in text:
        return "medium"
    return "normal"

def create_inbox_file(contact: str, message: str, timestamp: str) -> str:
    """Create .md file in Inbox folder"""
    priority = get_priority(message)
    file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_contact = re.sub(r"[^a-zA-Z0-9]", "_", contact[:30])
    filename = f"WHATSAPP_{safe_contact}_{file_timestamp}.md"
    filepath = INBOX_FOLDER / filename

    matched_keywords = [kw for kw in IMPORTANT_KEYWORDS if kw in message.lower()]

    content = f"""---
type: whatsapp_message
from: {contact}
subject: WhatsApp Message from {contact}
received: {timestamp}
priority: {priority}
status: pending
---

# WhatsApp Message Received

**From:** {contact}
**Priority:** {priority.upper()}
**Received:** {timestamp}
**Matched Keywords:** `{matched_keywords}`

---

## Message Content

{message}

---

## Required Actions
- [ ] Read message
- [ ] Orchestrator will create plan
- [ ] Orchestrator will move to Pending_Approval
- [ ] Human approval required
- [ ] Send via MCP after approval

---
*Detected by WhatsApp Watcher on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""
    try:
        filepath.write_text(content, encoding="utf-8")
        return filename
    except Exception as e:
        print(f"  ERROR creating file: {e}")
        return ""

def main():
    """Main WhatsApp monitoring loop"""
    print("=" * 60)
    print("WhatsApp Watcher - Silver Tier (Detection Only)")
    print("=" * 60)
    print(f"Session: {SESSION_PATH}")
    print(f"Inbox: {INBOX_FOLDER}")
    print(f"Keywords: {', '.join(IMPORTANT_KEYWORDS)}")
    print(f"Check interval: {CHECK_INTERVAL}s")
    print("-" * 60)
    print("WORKFLOW:")
    print("  1. This watcher detects messages → Creates files in Inbox/")
    print("  2. Orchestrator processes: Inbox → Needs_Action → Plans → Pending_Approval")
    print("  3. Human approves → Approved/")
    print("  4. Orchestrator sends via MCP → Done/")
    print("=" * 60)

    load_processed()

    try:
        with sync_playwright() as p:
            # Launch browser with persistent session
            log_msg = f"[{datetime.now().strftime('%H:%M:%S')}]"
            print(f"\n{log_msg} Launching WhatsApp Web...")

            context = p.chromium.launch_persistent_context(
                user_data_dir=str(SESSION_PATH),
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--remote-debugging-port=9223"
                ],
                viewport={"width": 1280, "height": 720}
            )

            page = context.pages[0] if context.pages else context.new_page()

            print(f"{log_msg} Navigating to WhatsApp Web...")
            try:
                page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"  Warning: {e}")

            print(f"{log_msg} Waiting for WhatsApp to load...")
            try:
                page.wait_for_selector('#pane-side, [data-testid="intro"]', timeout=120000)
                intro = page.query_selector('[data-testid="intro"]')
                if intro:
                    print(f"\n{'='*60}")
                    print("📱 FIRST TIME SETUP - QR CODE SCAN REQUIRED")
                    print(f"{'='*60}")
                    print("1. Open WhatsApp on your phone")
                    print("2. Tap Menu (⋮) or Settings")
                    print("3. Tap Linked Devices")
                    print("4. Tap Link a Device")
                    print("5. Scan the QR code in the browser")
                    print(f"{'='*60}")
                    print("\n⏳ Waiting 90 seconds for QR scan...")
                    time.sleep(90)

                    intro = page.query_selector('[data-testid="intro"]')
                    if intro:
                        print("\n❌ QR not scanned. Please scan and restart.")
                        context.close()
                        return
                    else:
                        print("\n✅ QR scanned successfully!")
                        print("💾 Session saved automatically!")
                else:
                    print(f"\n✅ WhatsApp session loaded! (Auto-login)")
            except PlaywrightTimeout:
                print("\n⚠️  Timeout - continuing anyway...")

            print(f"\n{'='*60}")
            print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] WhatsApp Watcher STARTED")
            print(f"🔍 Checking every {CHECK_INTERVAL} seconds")
            print(f"📥 New messages saved to: {INBOX_FOLDER}")
            print(f"🛑 Press Ctrl+C to stop")
            print(f"{'='*60}\n")

            while True:
                try:
                    # Scan chats - Try multiple selectors
                    chat_selectors = [
                        '#pane-side [role="row"]',
                        '#pane-side div[role="listitem"]',
                        '#pane-side div[data-testid="cell-frame-container"]'
                    ]

                    chats = []
                    selected_selector = ""
                    for sel in chat_selectors:
                        try:
                            chats = page.query_selector_all(sel)
                            if chats:
                                selected_selector = sel
                                break
                        except:
                            continue

                    if not chats:
                        time.sleep(CHECK_INTERVAL)
                        continue

                    new_messages = 0

                    for i, chat in enumerate(chats):
                        try:
                            # Try gridcell approach first
                            gridcells = chat.query_selector_all('[role="gridcell"]')
                            
                            # Fallback: try getting spans directly if gridcells < 3
                            if len(gridcells) < 3:
                                unread_badges = chat.query_selector_all('span[data-testid="unread-badge"]')
                                if unread_badges:
                                    try:
                                        name_elem = chat.query_selector('span[title]')
                                        contact = name_elem.get_attribute('title') if name_elem else f"Unknown_{i}"
                                        msg_elem = chat.query_selector('span[data-testid="last-message-content"]')
                                        message = msg_elem.inner_text() if msg_elem else ""
                                        
                                        # Check keywords
                                        msg_lower = message.lower()
                                        if not any(kw in msg_lower for kw in IMPORTANT_KEYWORDS):
                                            continue
                                            
                                        msg_id = f"{contact}:{message[:50]}"
                                        if msg_id in processed_messages:
                                            continue
                                            
                                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                        filename = create_inbox_file(contact, message, timestamp)
                                        if filename:
                                            save_processed(msg_id)
                                            new_messages += 1
                                            print(f"[{datetime.now().strftime('%H:%M:%S')}] 📩 New message from: {contact}")
                                            print(f"   Priority: {get_priority(message)}")
                                            print(f"   Saved to: Inbox/{filename}")
                                            print(f"   Message: {message[:80]}...\n")
                                    except Exception as e:
                                        continue
                                continue

                            # Get contact name from Cell 1
                            cell_1 = gridcells[1]
                            contact = "Unknown"
                            name_elem = cell_1.query_selector('span[dir="auto"]')
                            if name_elem:
                                contact = name_elem.inner_text() or "Unknown"
                            if contact == "Unknown":
                                name_elem = cell_1.query_selector('span[title]')
                                if name_elem:
                                    contact = name_elem.get_attribute('title') or "Unknown"

                            # Check unread count in Cell 2
                            cell_2 = gridcells[2]
                            cell_2_text = cell_2.inner_text().strip()
                            if not cell_2_text or not cell_2_text.isdigit():
                                continue

                            unread_count = int(cell_2_text)
                            if unread_count == 0:
                                continue

                            # Get message preview from Cell 0
                            cell_0 = gridcells[0]
                            message = ""
                            msg_elem = cell_0.query_selector('span[data-testid="last-message-content"]')
                            if msg_elem:
                                message = msg_elem.inner_text()

                            # Fallback: get longest span text
                            if not message:
                                spans = cell_0.query_selector_all('span')
                                for span in spans:
                                    try:
                                        text = span.inner_text()
                                        if text and len(text) > len(message):
                                            message = text
                                    except:
                                        continue

                            if not message.strip():
                                continue

                            # Check keywords
                            msg_lower = message.lower()
                            if not any(kw in msg_lower for kw in IMPORTANT_KEYWORDS):
                                continue

                            # Create unique ID
                            msg_id = f"{contact}:{message[:50]}"
                            if msg_id in processed_messages:
                                continue

                            # Create file in Inbox
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            filename = create_inbox_file(contact, message, timestamp)

                            if filename:
                                save_processed(msg_id)
                                new_messages += 1
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] 📩 New message from: {contact}")
                                print(f"   Priority: {get_priority(message)}")
                                print(f"   Saved to: Inbox/{filename}")
                                print(f"   Message: {message[:80]}...\n")

                            # Mark as read (click chat then escape)
                            try:
                                chat.click()
                                time.sleep(1)
                                page.keyboard.press("Escape")
                                time.sleep(0.5)
                            except:
                                pass

                        except Exception as e:
                            continue

                    if new_messages == 0:
                        pass  # Silent when no new messages

                    time.sleep(CHECK_INTERVAL)

                except Exception as e:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}")
                    time.sleep(5)

    except KeyboardInterrupt:
        print(f"\n\n{'='*60}")
        print("⏹️  WhatsApp Watcher Stopped")
        print(f"{'='*60}")
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            context.close()
        except:
            pass


if __name__ == "__main__":
    main()
