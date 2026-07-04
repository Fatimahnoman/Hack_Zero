"""
Debug WhatsApp DOM - Find actual selectors
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from playwright.sync_api import sync_playwright

SESSION_PATH = Path(__file__).parent.parent / "session" / "whatsapp"
WHATSAPP_URL = "https://web.whatsapp.com"
LOGS_FOLDER = Path(__file__).parent.parent / "Logs"

print("=" * 60)
print("WhatsApp DOM Debugger")
print("=" * 60)

with sync_playwright() as p:
    print("\n[1] Launching Chrome...")
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(SESSION_PATH),
        headless=False,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    
    page = context.pages[0] if context.pages else context.new_page()
    
    print("[2] Opening WhatsApp Web...")
    page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(10)
    
    print("[3] Analyzing DOM...\n")
    
    # Find all contenteditable elements
    editables = page.evaluate("""() => {
        const elements = document.querySelectorAll('[contenteditable="true"]');
        return Array.from(elements).map(el => ({
            tag: el.tagName,
            role: el.getAttribute('role'),
            placeholder: el.getAttribute('placeholder'),
            ariaLabel: el.getAttribute('aria-label'),
            textEditable: el.contentEditable,
            parent: el.parentElement?.tagName,
            classes: Array.from(el.classList).slice(0, 3)
        }));
    }""")
    
    print(f"Found {len(editables)} contenteditable elements:")
    for i, el in enumerate(editables):
        print(f"\n  [{i+1}] {el['tag']}")
        print(f"      Role: {el.get('role', 'N/A')}")
        print(f"      Placeholder: {el.get('placeholder', 'N/A')}")
        print(f"      Aria-label: {el.get('ariaLabel', 'N/A')}")
        print(f"      Classes: {el.get('classes', [])}")
    
    # Find input elements
    inputs = page.evaluate("""() => {
        const elements = document.querySelectorAll('input, textarea');
        return Array.from(elements).map(el => ({
            tag: el.tagName,
            type: el.type,
            placeholder: el.placeholder,
            ariaLabel: el.getAttribute('aria-label'),
            role: el.getAttribute('role'),
            id: el.id
        }));
    }""")
    
    print(f"\n\nFound {len(inputs)} input/textarea elements:")
    for i, el in enumerate(inputs[:10]):  # Show first 10
        print(f"\n  [{i+1}] {el['tag']}")
        print(f"      Type: {el.get('type', 'N/A')}")
        print(f"      Placeholder: {el.get('placeholder', 'N/A')}")
        print(f"      Role: {el.get('role', 'N/A')}")
    
    # Save full analysis
    analysis = {
        'contenteditable': editables,
        'inputs': inputs,
        'url': page.url,
        'title': page.title()
    }
    
    analysis_file = LOGS_FOLDER / "whatsapp_dom_analysis.json"
    analysis_file.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
    print(f"\n\nFull analysis saved to: {analysis_file}")
    
    # Take screenshot
    screenshot = LOGS_FOLDER / "whatsapp_current_state.png"
    page.screenshot(path=str(screenshot))
    print(f"Screenshot saved: {screenshot}")
    
    print("\n" + "=" * 60)
    print("Based on the output above, we can find the correct search box selector")
    print("=" * 60)
    
    time.sleep(5)
    context.close()
