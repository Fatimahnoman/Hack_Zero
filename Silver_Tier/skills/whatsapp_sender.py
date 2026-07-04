"""
WhatsApp Sender Agent Skill - Silver Tier (Ultimate Fix)
================================================================
Sends WhatsApp messages via Playwright CDP connection.
Focuses on robust chat opening and input detection.

Usage:
  python skills/whatsapp_sender.py --to "Contact Name" --message "Hello"
"""

import os
import sys
import time
import json
import datetime
import argparse
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    try:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    except:
        pass

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[ERROR] Playwright not installed")
    sys.exit(1)

# Configuration
VAULT_PATH = Path(__file__).parent.parent
SESSION_PATH = VAULT_PATH / 'session' / 'whatsapp'
LOGS_FOLDER = VAULT_PATH / 'Logs'
SESSION_PATH.mkdir(parents=True, exist_ok=True)
LOGS_FOLDER.mkdir(parents=True, exist_ok=True)


def send_whatsapp_message(target_contact: str, message_text: str) -> bool:
    """
    Send WhatsApp message by connecting to running Chrome (port 9223).
    """
    print(f'\n💬 WhatsApp Sender Agent')
    print(f'   Contact: {target_contact}')
    print(f'   Message length: {len(message_text)} chars')
    print()

    with sync_playwright() as p:
        browser = None

        try:
            # 1. Connect to running Chrome
            print('[1/5] Connecting to Chrome (port 9223)...')
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223", timeout=15000)
            print('      ✅ Connected')

            # 2. Find WhatsApp Tab
            context = browser.contexts[0]
            wa_page = None
            for page in context.pages:
                if 'web.whatsapp.com' in page.url:
                    wa_page = page
                    break

            if not wa_page:
                print('      ❌ No WhatsApp tab found!')
                return False

            # 3. Ensure we are at the main screen
            print('[2/5] Navigating to WhatsApp Web...')
            wa_page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)  # Wait for UI to settle

            # 4. Search and Open Contact
            print(f'[3/5] Searching for: {target_contact}')
            
            # Find search box
            search_selectors = [
                'input[placeholder="Search or start a new chat"]',
                'div[contenteditable="true"]'
            ]
            
            search_box = None
            for sel in search_selectors:
                try:
                    locator = wa_page.locator(sel).first
                    locator.wait_for(state='visible', timeout=8000)
                    search_box = locator
                    break
                except:
                    continue
            
            if not search_box:
                print('      ❌ Search box not found')
                return False

            # Type contact
            search_box.click()
            time.sleep(0.5)
            search_box.fill(target_contact)
            time.sleep(3)  # Wait for results

            # Open Chat - Pressing Enter is most reliable after typing
            print('      Pressing Enter to open chat...')
            wa_page.keyboard.press("Enter")
            
            # WAIT for chat to fully load - CRITICAL STEP
            print('      Waiting for chat to load...')
            time.sleep(6)
            
            # 5. Locate Message Input
            print(f'[4/5] Locating message input box...')
            
            # The message input is typically in the footer
            # Try specific footer selector first
            message_input = wa_page.locator('footer div[contenteditable="true"]').first
            
            try:
                message_input.wait_for(state='visible', timeout=10000)
                print('      ✅ Found message input box (footer)')
            except:
                # Fallback to generic contenteditable
                print('      ⚠️  Footer selector failed, trying generic...')
                # Use .first because the last one might be hidden or search box
                message_input = wa_page.locator('div[contenteditable="true"]').first
                message_input.wait_for(state='visible', timeout=10000)
                print('      ✅ Found message input box (generic)')
            
            # 6. Send Message
            print(f'[5/5] Sending message...')
            
            # Clear existing
            message_input.click()
            time.sleep(0.5)
            wa_page.keyboard.press('Control+a')
            time.sleep(0.2)
            wa_page.keyboard.press('Delete')
            time.sleep(0.2)
            
            # Type message
            print('      Typing message...')
            message_input.type(message_text, delay=50)
            time.sleep(1)
            
            # Send
            print('      Sending...')
            try:
                send_btn = wa_page.locator('span[data-icon="send"]').first
                send_btn.wait_for(state='visible', timeout=5000)
                send_btn.click()
                print('      ✅ Clicked Send Button')
            except:
                print('      Using Enter key...')
                wa_page.keyboard.press('Enter')
            
            time.sleep(3)
            print('      ✅ Message sent successfully!')
            return True

        except Exception as e:
            print(f'❌ ERROR: {e}')
            
            # Save debug screenshot
            try:
                if 'wa_page' in locals():
                    ss_path = LOGS_FOLDER / f'sender_error_{datetime.datetime.now().strftime("%H%M%S")}.png'
                    wa_page.screenshot(path=str(ss_path))
                    print(f'      Debug screenshot: {ss_path}')
            except:
                pass
            
            return False

        finally:
            if browser:
                try:
                    browser.disconnect()
                except:
                    pass


def main():
    parser = argparse.ArgumentParser(description='Send WhatsApp messages')
    parser.add_argument('--to', required=True)
    parser.add_argument('--message', required=True)
    args = parser.parse_args()

    print('=' * 60)
    print('WhatsApp Sender Agent - Silver Tier')
    print('=' * 60)

    success = send_whatsapp_message(args.to, args.message)
    
    if success:
        print("\n✅ SUCCESS! Message Sent.")
    else:
        print("\n❌ FAILED!")
    print('=' * 60)

    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
