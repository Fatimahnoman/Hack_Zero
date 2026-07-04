"""
Gmail Sender Agent Skill - Silver Tier (Real API Attempt)
===========================================================
Attempts to send an email using Gmail API.
Requires 'gmail.send' scope in credentials.
If permissions are missing, it will FAIL (keeping file in Approved/).

Usage:
  python skills/gmail_sender.py --to "recipient@email.com" --message "Body"
"""

import os
import sys
import base64
import json
import argparse
import datetime
from email.mime.text import MIMEText
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Configuration
VAULT_PATH = Path(__file__).parent.parent
TOKEN_FILE = VAULT_PATH / "token.json"
CREDENTIALS_FILE = VAULT_PATH / "credentials.json"
LOGS_FOLDER = VAULT_PATH / "Logs"
LOGS_FOLDER.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

def setup_credentials():
    """Load or create credentials with OAuth flow."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("[ERROR] Google libraries not installed.")
        return None

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"❌ credentials.json not found at {CREDENTIALS_FILE}")
                return None
            print("\n" + "=" * 60)
            print("GMAIL SENDER AUTHORIZATION REQUIRED")
            print("=" * 60)
            print("1. A browser window will open.")
            print("2. Sign in with your Google account.")
            print("3. Allow Gmail send access.")
            print("=" * 60 + "\n")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print("✅ Authentication successful! Token saved.")

    return build("gmail", "v1", credentials=creds)

def create_message(sender, to, subject, message_text):
    """Create a message for an email."""
    message = MIMEText(message_text)
    message["to"] = to
    message["from"] = sender
    message["subject"] = subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw_message}

def send_email(service, user_id, message):
    """Send an email message with proper error handling."""
    try:
        sent_message = service.users().messages().send(userId=user_id, body=message).execute()
        # Verify the message was actually sent by checking the response
        if sent_message and 'id' in sent_message:
            print(f"✅ Email sent successfully. Message ID: {sent_message['id']}")
            return True, sent_message['id']
        else:
            print("❌ API returned invalid response - email may not have been sent")
            return False, None
    except Exception as error:
        print(f"❌ API Error: {error}")
        print(f"❌ Email was NOT sent - check credentials and permissions")
        return False, None

def main():
    parser = argparse.ArgumentParser(description='Send Gmail messages')
    parser.add_argument('--to', required=True)
    parser.add_argument('--message', required=True)
    args = parser.parse_args()

    print(f"\n💬 Gmail Sender Agent (Real API)")
    print(f"   To: {args.to}")
    print(f"   Attempting to send...")

    service = setup_credentials()
    if not service:
        print("❌ FAILED: Could not setup credentials.")
        sys.exit(1)

    # Create a draft message
    # Note: 'me' is the authenticated user
    email_body = create_message("me", args.to, "AI Employee Reply", args.message)

    # Attempt Send
    success, message_id = send_email(service, "me", email_body)

    if success and message_id:
        print("✅ SUCCESS! Message Sent.")

        # Log success with actual confirmation
        log_entry = {
            'timestamp': datetime.datetime.now().isoformat(),
            'action': 'send_gmail_real',
            'to': args.to,
            'status': 'success',
            'message_id': message_id,
            'method': 'gmail_api_verified'
        }
        log_file = LOGS_FOLDER / f'gmail_{datetime.datetime.now().strftime("%Y%m%d")}.json'
        try:
            if log_file.exists():
                logs = json.loads(log_file.read_text())
            else:
                logs = []
            logs.append(log_entry)
            log_file.write_text(json.dumps(logs, indent=2))
        except Exception as e:
            print(f"⚠️  Warning: Could not write to log file: {e}")
            pass

        sys.exit(0)
    else:
        print("❌ FAILED: Message was NOT sent. File remains in Approved.")
        
        # Log failure
        log_entry = {
            'timestamp': datetime.datetime.now().isoformat(),
            'action': 'send_gmail_real',
            'to': args.to,
            'status': 'failed',
            'method': 'gmail_api_failed'
        }
        log_file = LOGS_FOLDER / f'gmail_{datetime.datetime.now().strftime("%Y%m%d")}.json'
        try:
            if log_file.exists():
                logs = json.loads(log_file.read_text())
            else:
                logs = []
            logs.append(log_entry)
            log_file.write_text(json.dumps(logs, indent=2))
        except Exception as e:
            pass
        
        sys.exit(1)

if __name__ == "__main__":
    main()
