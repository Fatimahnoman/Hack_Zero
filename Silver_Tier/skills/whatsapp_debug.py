"""
WhatsApp Debug - Check What's Actually Loaded
==============================================
Connects to running Chrome and shows current state.

Run: python skills\whatsapp_debug.py
"""

import sys
import time
from pathlib import Path

if sys.platform == 'win32':
    try:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    except:
        pass

from playwright.sync_api import sync_playwright

print("=" * 60)
print("WhatsApp Web Debugger")
print("=" * 60)

with sync_playwright() as p:
    try:
        print("\n[1] Connecting to Chrome (port 9223)...")
        browser = p.chromium.connect_over_cdp("http://localhost:9223", timeout=10000)
        print("    ✅ Connected!")

        context = browser.contexts[0]
        pages = context.pages

        print(f"\n[2] Found {len(pages)} tab(s):")
        for i, page in enumerate(pages):
            print(f"    Tab {i+1}: {page.url}")
            print(f"           Title: {page.title()}")

        # Find WhatsApp page
        wa_page = None
        for page in pages:
            if 'web.whatsapp.com' in page.url:
                wa_page = page
                break

        if not wa_page:
            print("\n❌ No WhatsApp Web tab found!")
            print("   Open https://web.whatsapp.com manually in the browser")
            browser.disconnect()
            sys.exit(1)

        print(f"\n[3] WhatsApp Web page found")
        print(f"    URL: {wa_page.url}")

        # Take screenshot
        vault_path = Path(__file__).parent.parent
        screenshot_path = vault_path / 'Logs' / 'whatsapp_debug_check.png'
        wa_page.screenshot(path=str(screenshot_path))
        print(f"\n    Screenshot saved: {screenshot_path}")

        # Check page state
        print(f"\n[4] Checking WhatsApp state...")
        
        # Check for various elements
        checks = {
            "Chat list": "[data-testid='chat-list']",
            "Search box": "div[contenteditable='true']",
            "Intro screen": "[data-testid='intro']",
            "QR code": "canvas",
            "Send button": "span[data-icon='send']",
            "Message input": "footer div[contenteditable='true']",
        }

        for name, selector in checks.items():
            try:
                el = wa_page.query_selector(selector)
                if el and el.is_visible():
                    print(f"    ✅ {name}: Found")
                else:
                    print(f"    ❌ {name}: Not visible")
            except Exception as e:
                print(f"    ❌ {name}: Error - {str(e)[:50]}")

        print(f"\n[5] Testing search box interaction...")
        try:
            search_box = wa_page.locator("div[contenteditable='true']").first
            search_box.wait_for(state='visible', timeout=5000)
            print("    ✅ Search box found and visible!")
            
            # Try to click
            search_box.click()
            time.sleep(0.5)
            print("    ✅ Search box clickable!")
        except Exception as e:
            print(f"    ❌ Search box error: {e}")

        browser.disconnect()
        print("\n✅ Debug complete!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\n💡 Make sure WhatsApp Watcher is running!")
