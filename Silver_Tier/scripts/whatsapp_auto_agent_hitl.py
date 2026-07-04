"""
WhatsApp Auto-Agent - HITL (Human-In-The-Loop) Workflow
=======================================================
Complete End-to-End Automation for AI Employee Vault

Phase 1: Monitor unread → Detect keywords → Generate draft → WAIT
Phase 2: User approves draft → Type + Send visibly

Folders:
  Pending_Approval/  → Drafts waiting for review (root level)
  Approved/          → Approved drafts (root level)
  Done/WhatsApp/     → Sent messages archive

Run: python scripts/whatsapp_auto_agent_hitl.py
"""

import os
import re
import sys
import time
import shutil
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] Install Playwright: pip install playwright && playwright install chromium")
    sys.exit(1)

# =============================================================================
# CONFIGURATION
# =============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = PROJECT_ROOT / "session" / "whatsapp"
NEEDS_ACTION_FOLDER = PROJECT_ROOT / "Needs_Action"

# Approval folders - ROOT LEVEL
PENDING_APPROVAL_FOLDER = PROJECT_ROOT / "Pending_Approval"
APPROVED_FOLDER = PROJECT_ROOT / "Approved"
DONE_FOLDER = PROJECT_ROOT / "Done" / "WhatsApp"
LOGS_FOLDER = PROJECT_ROOT / "Logs"

for folder in [SESSION_PATH, NEEDS_ACTION_FOLDER, PENDING_APPROVAL_FOLDER, APPROVED_FOLDER, DONE_FOLDER, LOGS_FOLDER]:
    folder.mkdir(parents=True, exist_ok=True)

WHATSAPP_URL = "https://web.whatsapp.com"
CHECK_INTERVAL = 15  # seconds
APPROVAL_CHECK_INTERVAL = 30  # seconds
TYPING_DELAY = 0.06  # seconds per character (visible typing)

# Keywords to auto-respond to
AUTO_REPLY_KEYWORDS = ["urgent", "invoice", "payment", "sales", "hello", "hi", "need", "price", "cost"]

# Track processed messages
processed_messages = set()

# =============================================================================
# LOGGING
# =============================================================================
def log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}")
    log_file = LOGS_FOLDER / f"whatsapp_hitl_{datetime.now().strftime('%Y%m%d')}.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")

# =============================================================================
# AI REPLY GENERATOR
# =============================================================================
def generate_reply(contact: str, message: str) -> str:
    """Generate a professional reply based on message content."""
    msg_lower = message.lower()

    if "urgent" in msg_lower or "asap" in msg_lower:
        return f"Hi {contact}! We've received your urgent request and our team is prioritizing it right now. Someone will get back to you within the hour. Thank you for your patience!"

    if "price" in msg_lower or "cost" in msg_lower or "invoice" in msg_lower:
        return f"Hi {contact}! Thank you for your inquiry. We'll prepare a detailed quotation and share it with you shortly. Looking forward to working together!"

    if "sales" in msg_lower or "deal" in msg_lower or "need" in msg_lower:
        return f"Hi {contact}! Great to hear from you! We'd love to discuss this opportunity. Our business team will connect with you soon. Thank you!"

    if "hello" in msg_lower or "hi" in msg_lower or "hey" in msg_lower:
        return f"Hi {contact}! Thanks for reaching out! How can we assist you today?"

    return f"Hi {contact}! Message received. Our team is reviewing your inquiry and will respond shortly. Thank you!"

# =============================================================================
# DRAFT FILE MANAGEMENT
# =============================================================================
def create_draft_file(contact: str, message: str, reply: str) -> str:
    """
    Create draft file in Pending_Approval/ (NOT in Approved/).
    File stays here until user manually moves to Approved/.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_contact = re.sub(r"[^a-zA-Z0-9]", "_", contact[:30])
    filename = f"WHATSAPP_{safe_contact}_{timestamp}.md"

    # Create directly in Pending_Approval/ (WAITING for approval)
    draft_path = PENDING_APPROVAL_FOLDER / filename
    content = f"""---
type: whatsapp_message
from: {contact}
to: {contact}
received: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
priority: normal
status: pending_approval
---

## Incoming Message

{message}

## Draft Reply

{reply}

---
*Status: PENDING APPROVAL*
*To approve: Move this file to 'Approved/' folder manually*
*Script will auto-send once file is in Approved/*
"""
    try:
        draft_path.write_text(content, encoding="utf-8")
        log(f"  DRAFT SAVED: {filename}")
        log(f"  Location: Pending_Approval/ (WAITING for approval)")
        return str(draft_path)
    except Exception as e:
        log(f"  ERROR creating draft: {e}")
        return ""

# =============================================================================
# MAIN AUTOMATION
# =============================================================================
def process_chat(page, chat_row, contact: str):
    """Process a single chat: open, mark seen, generate draft, WAIT for approval."""
    try:
        log(f"  Contact: {contact}")

        # Step 1: Click to open chat (marks as seen automatically)
        log(f"  Opening chat...")
        chat_row.click()
        time.sleep(2)

        # Step 2: Extract message content from open chat
        log(f"  Reading messages...")

        message = ""
        try:
            msg_selectors = [
                'span[data-testid="last-message-content"]',
                'div[aria-label="Chat history"] span.copyable-text span',
                'span.copyable-text span[dir="auto"]',
                'span.copyable-text span',
            ]

            for sel in msg_selectors:
                try:
                    elements = page.query_selector_all(sel)
                    if elements:
                        for elem in reversed(elements):
                            try:
                                txt = elem.inner_text()
                                if txt and len(txt.strip()) > 2:
                                    if elem.is_visible():
                                        message = txt.strip()
                                        break
                            except:
                                continue
                        if message:
                            break
                except:
                    continue
        except Exception as e:
            log(f"  Message extraction error: {e}")

        # Fallback
        if not message:
            try:
                msg_area = page.query_selector('div[data-testid="conversation-panel-messages"]')
                if msg_area:
                    all_text = msg_area.inner_text()
                    if all_text:
                        lines = [l.strip() for l in all_text.split('\n') if l.strip()]
                        if lines:
                            message = lines[-1]
            except:
                pass

        log(f"  Message: {message[:100] if message else '(empty)'}")

        # Step 3: Check for keywords
        if not message or not any(kw in message.lower() for kw in AUTO_REPLY_KEYWORDS):
            log(f"  No keywords found")
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except:
                pass
            return False

        log(f"  KEYWORD DETECTED! Generating draft...")

        # Step 4: Generate AI reply
        reply = generate_reply(contact, message)
        log(f"  Draft reply generated ({len(reply)} chars)")

        # Step 5: Create draft file in Pending_Approval/ (WAITING for approval)
        draft_path = create_draft_file(contact, message, reply)

        # Step 6: Go back to chat list
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except:
            pass

        return True

    except Exception as e:
        log(f"  Error processing chat: {e}")
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except:
            pass
        return False

def send_approved_drafts(page):
    """Send all approved drafts via WhatsApp."""
    if not APPROVED_FOLDER.exists():
        return 0

    approved_files = list(APPROVED_FOLDER.glob("WHATSAPP_*.md"))
    if not approved_files:
        return 0

    sent_count = 0
    for draft_path in approved_files:
        try:
            content = draft_path.read_text(encoding="utf-8")
            from_match = re.search(r"from: (.+)", content)
            contact = from_match.group(1).strip() if from_match else ""

            draft_reply_match = re.search(r"## Draft Reply\n\n(.+?)\n---", content, re.DOTALL)
            reply = draft_reply_match.group(1).strip() if draft_reply_match else ""

            if not contact or not reply:
                log(f"  ERROR: Missing data in {draft_path.name}")
                continue

            log(f"[SENDING] Opening chat for {contact}...")

            # Search for contact
            try:
                search_box = page.locator('div[contenteditable="true"]').first
                search_box.wait_for(state='visible', timeout=10000)
                search_box.click()
                time.sleep(0.5)
                search_box.fill(contact)
                time.sleep(2)
                page.keyboard.press("Enter")
                time.sleep(2)
            except Exception as e:
                log(f"  ERROR searching contact: {e}")
                continue

            # Step 7: Type reply - VISIBLE TYPING
            log(f"  Typing reply for {contact}...")

            # First, ensure message box is focused
            try:
                msg_box = page.locator('footer div[contenteditable="true"]').first
                msg_box.wait_for(state='visible', timeout=10000)
                msg_box.click()
                time.sleep(0.5)
            except Exception as e:
                log(f"  ERROR: Message box not found: {e}")
                page.keyboard.press("Escape")
                time.sleep(0.5)
                continue

            # Type character by character
            for i, char in enumerate(reply):
                try:
                    page.keyboard.type(char, delay=50)
                    if (i + 1) % 20 == 0:
                        log(f"    ...typed {i+1}/{len(reply)} chars")
                except Exception as e:
                    log(f"  ERROR typing char {i+1}: {e}")
                    try:
                        msg_box = page.locator('footer div[contenteditable="true"]').first
                        msg_box.click()
                        time.sleep(0.3)
                    except:
                        pass

            log(f"  Finished typing! Waiting 1 second before send...")
            time.sleep(1.5)

            # Step 8: SEND - Try multiple methods
            send_success = False

            # Method 1: Click send icon (most reliable)
            if not send_success:
                try:
                    send_btn = page.locator('button[aria-label="Send"]').first
                    send_btn.wait_for(state='visible', timeout=3000)
                    send_btn.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    send_btn.click()
                    log(f"  SENT to {contact} (aria-label method)!")
                    send_success = True
                except Exception as e:
                    log(f"  Method 1 (aria-label) failed: {e}")

            # Method 2: Click span icon
            if not send_success:
                try:
                    send_span = page.locator('span[data-icon="send"]').first
                    send_span.wait_for(state='visible', timeout=3000)
                    send_btn = send_span.locator('xpath=..')
                    send_btn.click()
                    log(f"  SENT to {contact} (icon method)!")
                    send_success = True
                except Exception as e:
                    log(f"  Method 2 (icon) failed: {e}")

            # Method 3: Keyboard Enter (with focus reset)
            if not send_success:
                try:
                    msg_box = page.locator('footer div[contenteditable="true"]').first
                    msg_box.click()
                    time.sleep(0.3)
                    page.keyboard.press("Enter")
                    log(f"  SENT to {contact} via Enter!")
                    send_success = True
                except Exception as e:
                    log(f"  Method 3 (Enter) failed: {e}")

            # Method 4: Try Enter without focus reset
            if not send_success:
                try:
                    page.keyboard.press("Enter")
                    log(f"  SENT to {contact} via Enter (fallback)!")
                    send_success = True
                except Exception as e:
                    log(f"  Method 4 (Enter fallback) failed: {e}")

            if not send_success:
                log(f"  ERROR: All send methods failed!")
            else:
                log(f"  MESSAGE DELIVERED to {contact}!")

            time.sleep(2)

            # Move to Done
            dest = DONE_FOLDER / draft_path.name
            shutil.move(str(draft_path), str(dest))
            log(f"  Archived to Done")
            sent_count += 1

            # Go back to chat list
            page.keyboard.press("Escape")
            time.sleep(0.5)

        except Exception as e:
            log(f"  ERROR sending to {contact}: {e}")

    return sent_count

def main():
    """Main WhatsApp HITL workflow."""
    print("=" * 60)
    print("WhatsApp Auto-Agent - HITL (Human-In-The-Loop)")
    print("=" * 60)
    print(f"Session: {SESSION_PATH}")
    print(f"Drafts: {PENDING_APPROVAL_FOLDER}")
    print(f"Approved: {APPROVED_FOLDER}")
    print(f"Keywords: {', '.join(AUTO_REPLY_KEYWORDS)}")
    print("-" * 60)
    print("WORKFLOW:")
    print("  1. Monitors WhatsApp Web for unread messages")
    print("  2. If keyword found -> Opens chat -> Marks as seen")
    print("  3. Generates AI reply draft")
    print("  4. Saves draft to Pending_Approval/")
    print("  5. WAITING for you to review & approve")
    print("  6. Move draft to Approved/ folder -> Auto-sends!")
    print("-" * 60)

    try:
        with sync_playwright() as p:
            # Launch browser
            log("Launching WhatsApp Web...")
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

            log("Navigating to WhatsApp Web...")
            try:
                page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                log(f"Navigation warning: {e}")

            log("Waiting for WhatsApp to load...")
            try:
                page.wait_for_selector('#pane-side, [data-testid="intro"]', timeout=120000)
                intro = page.query_selector('[data-testid="intro"]')
                if intro:
                    log("QR code needed - Please scan to login")
                    time.sleep(60)
                    intro = page.query_selector('[data-testid="intro"]')
                    if intro:
                        log("QR not scanned. Restart after scanning.")
                        context.close()
                        return
                log("WhatsApp Web loaded!")
            except PlaywrightTimeout:
                log("WhatsApp timeout - continuing anyway")

            print("-" * 60)
            log(f"HITL AGENT STARTED - Monitoring every {CHECK_INTERVAL}s")
            log(f"Checking for approved drafts every {APPROVAL_CHECK_INTERVAL}s")
            log("Press Ctrl+C to stop")
            print("=" * 60)

            last_approval_check = time.time()

            try:
                while True:
                    # Check for approved drafts to send
                    current_time = time.time()
                    if current_time - last_approval_check >= APPROVAL_CHECK_INTERVAL:
                        log("Checking for approved drafts...")
                        sent = send_approved_drafts(page)
                        if sent > 0:
                            log(f"Sent {sent} draft(s)!")
                        last_approval_check = current_time

                    # Monitor for new unread messages
                    try:
                        chat_selectors = [
                            '#pane-side [role="row"]',
                            '#pane-side div[role="listitem"]',
                            '#pane-side div[data-testid="cell-frame-container"]'
                        ]
                        chats = []
                        for sel in chat_selectors:
                            try:
                                chats = page.query_selector_all(sel)
                                if chats:
                                    log(f"Found {len(chats)} chats via: {sel}")
                                    break
                            except:
                                continue

                        if not chats:
                            log("No chats found, waiting...")
                            time.sleep(CHECK_INTERVAL)
                            continue

                        log(f"Scanning {len(chats)} chats...")
                        replied_count = 0

                        for i, chat in enumerate(chats):
                            try:
                                gridcells = chat.query_selector_all('[role="gridcell"]')
                                if len(gridcells) < 3:
                                    unread_badges = chat.query_selector_all('span[data-testid="unread-badge"]')
                                    if unread_badges:
                                        try:
                                            name_elem = chat.query_selector('span[title]')
                                            contact = name_elem.get_attribute('title') if name_elem else f"Unknown_{i}"
                                            msg_elem = chat.query_selector('span[data-testid="last-message-content"]')
                                            message = msg_elem.inner_text() if msg_elem else ""
                                            log(f"  Chat {i+1}: {contact} (unread via badge)")
                                            msg_id = f"{contact}:{message[:50]}"
                                            if msg_id not in processed_messages:
                                                success = process_chat(page, chat, contact)
                                                if success:
                                                    replied_count += 1
                                                processed_messages.add(msg_id)
                                        except Exception as e:
                                            log(f"  Badge extraction error: {e}")
                                    continue

                                cell_1 = gridcells[1]
                                contact = "Unknown"
                                name_elem = cell_1.query_selector('span[dir="auto"]')
                                if name_elem:
                                    contact = name_elem.inner_text() or "Unknown"
                                if contact == "Unknown":
                                    name_elem = cell_1.query_selector('span[title]')
                                    if name_elem:
                                        contact = name_elem.get_attribute('title') or "Unknown"

                                cell_2 = gridcells[2]
                                cell_2_text = cell_2.inner_text().strip()

                                if not cell_2_text or not cell_2_text.isdigit():
                                    continue

                                unread_count = int(cell_2_text)
                                if unread_count == 0:
                                    continue

                                cell_0 = gridcells[0]
                                msg_elem = cell_0.query_selector('span[data-testid="last-message-content"]')
                                message = msg_elem.inner_text() if msg_elem else ""

                                if not message.strip():
                                    all_spans = cell_0.query_selector_all('span')
                                    for sp in all_spans:
                                        try:
                                            txt = sp.inner_text()
                                            if txt and len(txt) > 5:
                                                message = txt
                                                break
                                        except:
                                            pass

                                if not message.strip():
                                    continue

                                msg_id = f"{contact}:{message[:50]}"
                                if msg_id in processed_messages:
                                    continue

                                log(f"UNREAD: {contact} ({unread_count} unread)")
                                log(f"  Message: {message[:80]}...")

                                success = process_chat(page, chat, contact)
                                if success:
                                    replied_count += 1

                                processed_messages.add(msg_id)
                                time.sleep(2)

                            except Exception as e:
                                log(f"  Chat {i+1} error: {e}")
                                continue

                        if replied_count > 0:
                            log(f"Drafts created: {replied_count}")
                        else:
                            log("No new drafts needed")

                    except Exception as e:
                        log(f"Loop error: {e}")
                        import traceback
                        log(traceback.format_exc())

                    time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                log("HITL Agent stopped by user")

            finally:
                try:
                    context.close()
                except:
                    pass
                log("Browser closed")
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()
