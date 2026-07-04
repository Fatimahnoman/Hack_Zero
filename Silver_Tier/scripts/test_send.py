"""
Quick Send Test - Test WhatsApp sending directly
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from whatsapp_watcher import send_message_via_browser, WHATSAPP_URL, LOGS_FOLDER
from playwright.sync_api import sync_playwright

print("=" * 60)
print("WhatsApp Send Test")
print("=" * 60)

SESSION_PATH = Path(__file__).parent.parent / "session" / "whatsapp"

with sync_playwright() as p:
    print("\n[1] Launching Chrome with existing session...")
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(SESSION_PATH),
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    
    page = context.pages[0] if context.pages else context.new_page()
    
    print("[2] Opening WhatsApp Web...")
    page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(8)  # Wait for full load
    
    print("[3] Testing send to 'Baba Jaan'...")
    success = send_message_via_browser(
        page,
        "Baba Jaan",
        "Test message from Silver Tier - please ignore!"
    )
    
    if success:
        print("\n✅ SUCCESS! Message sent!")
    else:
        print("\n❌ FAILED! Check debug screenshot in Logs/")
    
    context.close()

print("\n" + "=" * 60)
input("Press Enter to exit...")
