"""
WhatsApp MCP Server - Silver Tier (Working Version)
====================================================
Launches Chrome, waits for CDP, sends message.

Usage:
  python mcp_servers/whatsapp_mcp.py "VAULT_PATH" "Contact Name" "Message"
"""

import sys
import time
import json
import codecs
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime


def wait_for_cdp(port, timeout=30):
    """Wait until Chrome CDP port is ready"""
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2)
            if response.status == 200:
                return True
        except:
            pass
        time.sleep(0.5)
    return False


def send_whatsapp_message(vault_path: str, contact_name: str, message: str) -> dict:
    """Send WhatsApp message"""
    try:
        if sys.platform == 'win32':
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

        print('[MCP] Starting WhatsApp MCP...')

        vault = Path(vault_path)
        logs_path = vault / 'Logs'
        session_path = vault / 'sessions' / 'whatsapp'
        logs_path.mkdir(parents=True, exist_ok=True)
        session_path.mkdir(parents=True, exist_ok=True)

        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not Path(chrome_path).exists():
            chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

        # Launch Chrome via subprocess
        cdp_port = 9444
        cmd = [
            chrome_path,
            f'--user-data-dir={session_path}',
            f'--remote-debugging-port={cdp_port}',
            '--no-first-run',
            '--no-default-browser-check',
            '--start-maximized',
            'https://web.whatsapp.com'
        ]

        print(f'[MCP] Launching Chrome...')
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for CDP to be ready
        print(f'[MCP] Waiting for Chrome to start (up to 30s)...')
        if not wait_for_cdp(cdp_port, timeout=30):
            print('[MCP] ERROR: Chrome did not start in time')
            return {'status': 'error', 'message': 'Chrome startup timeout'}

        print('[MCP] Chrome is ready!')

        # Connect via Playwright CDP
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            print(f'[MCP] Connecting to Chrome via CDP...')
            browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{cdp_port}', timeout=15000)

            context = browser.contexts[0]
            pages = context.pages

            # Find WhatsApp Web page
            wa_page = None
            for page in pages:
                if 'web.whatsapp.com' in page.url:
                    wa_page = page
                    break

            if not wa_page:
                wa_page = context.new_page()
                wa_page.goto('https://web.whatsapp.com', wait_until='domcontentloaded', timeout=30000)
                time.sleep(5)

            print('[MCP] WhatsApp Web ready!')
            print(f'[MCP] Sending to: {contact_name}')
            print('[MCP] Watch browser for typing animation')
            print()

            # Step 1: Search
            print('[MCP] Step 1/3: Searching for contact...')
            search_box = wa_page.query_selector('div[contenteditable="true"]')
            if not search_box:
                print('[MCP] ERROR: Search box not found')
                wa_page.screenshot(path=str(logs_path / 'mcp_debug.png'))
                browser.close()
                return {'status': 'error', 'message': 'Search box not found'}

            search_box.click()
            time.sleep(0.5)
            wa_page.keyboard.press('Control+a')
            time.sleep(0.2)
            wa_page.keyboard.press('Delete')
            time.sleep(0.3)
            search_box.fill(contact_name)
            time.sleep(2)

            first_result = wa_page.query_selector('span[title]')
            if first_result:
                first_result.click()
                print(f'[MCP] Contact selected: {contact_name}')
            time.sleep(1)

            # Step 2: Type
            print('[MCP] Step 2/3: Typing message (watch for animation)...')
            message_box = wa_page.query_selector('footer div[contenteditable="true"]')
            if not message_box:
                message_box = wa_page.query_selector('div[contenteditable="true"]')

            message_box.click()
            time.sleep(0.3)

            for i, char in enumerate(message):
                message_box.type(char, delay=50)
                if (i + 1) % 20 == 0:
                    print(f'[MCP]      Typed {i+1}/{len(message)} chars...')

            time.sleep(0.5)

            # Step 3: Send
            print('[MCP] Step 3/3: Sending...')
            send_button = wa_page.query_selector('span[data-icon="send"]')
            if send_button:
                send_button.click()
                print('[MCP] Clicked send button')
            else:
                wa_page.keyboard.press('Enter')
                print('[MCP] Pressed Enter')

            time.sleep(2)
            print('[MCP] SUCCESS: Message sent!')

            # Log
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'action': 'send_whatsapp_message',
                'contact': contact_name,
                'message_preview': message[:150],
                'message_length': len(message),
                'status': 'success',
                'method': 'cdp_subprocess_v2'
            }

            log_file = logs_path / f'mcp_{datetime.now().strftime("%Y%m%d")}.json'
            try:
                if log_file.exists():
                    logs = json.loads(log_file.read_text())
                else:
                    logs = []
                logs.append(log_entry)
                log_file.write_text(json.dumps(logs, indent=2))
            except:
                pass

            browser.close()

            return {
                'status': 'success',
                'message': f'Message sent to {contact_name}',
                'contact': contact_name,
                'message_length': len(message),
                'timestamp': datetime.now().isoformat()
            }

    except Exception as e:
        print(f'[MCP] ERROR: {e}')
        import traceback
        traceback.print_exc()
        return {'status': 'error', 'message': str(e)}


def main():
    if len(sys.argv) < 4:
        print('Usage: python whatsapp_mcp.py "VAULT" "Contact" "Message"')
        sys.exit(1)

    vault_path = sys.argv[1]
    contact_name = sys.argv[2]
    message = sys.argv[3]

    print('=' * 60)
    print('WhatsApp MCP Server - Silver Tier')
    print('=' * 60)
    print()

    result = send_whatsapp_message(vault_path, contact_name, message)

    print()
    print('=' * 60)
    print('Result:', json.dumps(result, indent=2))
    print('=' * 60)

    sys.exit(0 if result['status'] == 'success' else 1)


if __name__ == '__main__':
    main()
