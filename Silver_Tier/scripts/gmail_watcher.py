"""
Gmail Watcher - Silver Tier
============================
Monitors Gmail inbox for unread important emails with specific keywords.
Uses Google Gmail API with OAuth2 credentials.

Install: pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
Setup:
  1. credentials.json is already in project root.
  2. Run script once to authorize (token.json will be created).
Run: python scripts/gmail_watcher.py
"""

import os
import re
import sys
import time
import base64
import logging
from datetime import datetime
from pathlib import Path

# Check for required dependencies
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError as e:
    print(f"[ERROR] Missing required dependency: {e}")
    print("[INFO] Please install required packages:")
    print("       pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")
    sys.exit(1)

# Configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.json"
INBOX_FOLDER = PROJECT_ROOT / "Inbox"
NEEDS_ACTION_FOLDER = PROJECT_ROOT / "Needs_Action"
LOGS_FOLDER = PROJECT_ROOT / "Logs"
LOG_FILE = LOGS_FOLDER / "gmail_watcher.log"
CHECK_INTERVAL = 60  # seconds
STATUS_INTERVAL = 30  # seconds
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]

# Keywords to monitor (case-insensitive)
IMPORTANT_KEYWORDS = ["urgent", "asap", "invoice", "payment", "help"]

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def setup_logging():
    """Setup logging to file and console."""
    if not LOGS_FOLDER.exists():
        LOGS_FOLDER.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


logger = setup_logging()


def get_priority(subject: str, snippet: str) -> str:
    """Determine priority based on keywords."""
    text = f"{subject} {snippet}".lower()
    if "urgent" in text:
        return "high"
    elif "invoice" in text or "payment" in text:
        return "medium"
    elif "sales" in text:
        return "normal"
    return "low"


def decode_message(message: str) -> str:
    """Decode base64url encoded message."""
    try:
        return base64.urlsafe_b64decode(message + "=" * (-len(message) % 4)).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Error decoding message: {e}")
        return ""


def extract_email_data(msg: dict) -> dict:
    """Extract relevant data from Gmail message."""
    headers = msg["payload"]["headers"]

    data = {
        "from": "",
        "subject": "",
        "date": "",
        "snippet": msg.get("snippet", "")
    }

    for header in headers:
        name = header["name"].lower()
        if name == "from":
            data["from"] = header["value"]
        elif name == "subject":
            data["subject"] = header["value"]
        elif name == "date":
            data["date"] = header["value"]

    # Get full message body if available
    body = ""
    if "parts" in msg["payload"]:
        for part in msg["payload"]["parts"]:
            if part["mimeType"] == "text/plain" and "data" in part["body"]:
                body = decode_message(part["body"]["data"])
                break
    elif "body" in msg["payload"] and "data" in msg["payload"]["body"]:
        body = decode_message(msg["payload"]["body"]["data"])

    data["body"] = body
    return data


def create_needs_action_file(email_data: dict, msg_id: str) -> str:
    """Create .md file in Inbox folder with YAML frontmatter."""
    priority = get_priority(email_data["subject"], email_data["snippet"])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Sanitize filename
    safe_subject = re.sub(r"[^a-zA-Z0-9]", "_", email_data["subject"][:30])
    filename = f"GMAIL_{safe_subject}_{timestamp}.md"
    filepath = INBOX_FOLDER / filename

    # Parse received date
    received = email_data["date"]
    received_formatted = received
    try:
        received_dt = datetime.strptime(received.split(",")[1].strip()[:19], "%d %b %Y %H:%M:%S")
        received_formatted = received_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    yaml_content = f"""---
type: email
from: {email_data["from"]}
subject: {email_data["subject"]}
received: {received_formatted}
priority: {priority}
status: pending
gmail_id: {msg_id}
---

## Email Content

{email_data["body"] if email_data["body"] else email_data["snippet"]}

---
*Imported by Gmail Watcher on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    logger.info(f"Created file: {filename}")
    return filename


def authenticate_gmail():
    """Authenticate and build Gmail API service."""
    creds = None

    # Load existing token
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Refresh or request new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_FILE}\n"
                    "Please download it from Google Cloud Console and place it in project root."
                )
            logger.info("Starting OAuth flow...")
            print("\n" + "=" * 60)
            print("GMAIL AUTHORIZATION REQUIRED")
            print("=" * 60)
            print("1. A browser window will open.")
            print("2. Sign in with your Google account.")
            print("3. Allow access to Gmail.")
            print("=" * 60 + "\n")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for next run
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        logger.info("Credentials saved to token.json")
        print("✅ Authentication successful! Token saved.")

    return build("gmail", "v1", credentials=creds)


def check_gmail(service, processed_ids: set) -> int:
    """Check Gmail for new important unread emails with retry logic."""
    new_count = 0

    for attempt in range(MAX_RETRIES):
        try:
            # Build query with keyword filter at API level
            keyword_query = " OR ".join(IMPORTANT_KEYWORDS)
            query = f"is:unread ({keyword_query})"

            results = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=50
            ).execute()

            messages = results.get("messages", [])

            for msg in messages:
                msg_id = msg["id"]

                # Skip if already processed
                if msg_id in processed_ids:
                    continue

                # Get full message
                message = service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full"
                ).execute()

                email_data = extract_email_data(message)

                # Double-check keywords in subject/snippet
                text_to_check = f"{email_data['subject']} {email_data['snippet']}".lower()
                if not any(keyword in text_to_check for keyword in IMPORTANT_KEYWORDS):
                    continue

                filename = create_needs_action_file(email_data, msg_id)
                logger.info(f"NEW IMPORTANT EMAIL - From: {email_data['from']}, Subject: {email_data['subject']}")
                print(f"  -> Created: {filename}")
                print(f"     From: {email_data['from']}")
                print(f"     Subject: {email_data['subject']}")
                print(f"     Priority: {get_priority(email_data['subject'], email_data['snippet'])}")
                new_count += 1

                # Add to processed set
                processed_ids.add(msg_id)

            # Success - break retry loop
            break

        except HttpError as error:
            logger.warning(f"Gmail API error (attempt {attempt + 1}/{MAX_RETRIES}): {error}")
            if attempt < MAX_RETRIES - 1:
                logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries reached. Continuing to next check cycle.")

        except Exception as e:
            logger.error(f"Unexpected error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                logger.error("Max retries reached. Continuing to next check cycle.")
                break

    return new_count


def ensure_directories():
    """Ensure required directories exist."""
    INBOX_FOLDER.mkdir(parents=True, exist_ok=True)
    NEEDS_ACTION_FOLDER.mkdir(parents=True, exist_ok=True)
    LOGS_FOLDER.mkdir(parents=True, exist_ok=True)


def main():
    """Main function to start Gmail watcher."""
    print("=" * 60)
    print("Gmail Watcher - Silver Tier")
    print("=" * 60)
    print(f"Monitoring: Gmail Inbox (unread)")
    print(f"Keywords: {', '.join(IMPORTANT_KEYWORDS)}")
    print(f"Destination: {INBOX_FOLDER}")
    print(f"Check interval: {CHECK_INTERVAL} seconds")
    print(f"Log file: {LOG_FILE}")
    print("-" * 60)

    # Ensure directories exist
    ensure_directories()

    # Check for credentials
    if not CREDENTIALS_FILE.exists():
        logger.error(f"credentials.json not found at {CREDENTIALS_FILE}")
        print("")
        print("SETUP INSTRUCTIONS:")
        print("credentials.json is required.")
        sys.exit(1)

    print("Press Ctrl+C to stop the watcher...")
    print("=" * 60)

    # Authenticate
    try:
        print("[INFO] Authenticating with Gmail API...")
        logger.info("Starting Gmail authentication...")
        service = authenticate_gmail()
        print("[INFO] Authentication successful!")
        logger.info("Authentication successful!")
    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        print("")
        print("TROUBLESHOOTING:")
        print("1. Delete token.json if it exists and try again")
        print("2. Make sure credentials.json is valid")
        print("3. Check that Gmail API is enabled in Google Cloud Console")
        sys.exit(1)

    # Track processed message IDs
    processed_ids = set()

    logger.info("Watcher started successfully!")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Watcher started successfully!")
    print("-" * 60)

    # Status counter for ONLINE message
    status_counter = 0

    try:
        while True:
            # Check for new emails
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking Gmail...")
            logger.info("Checking Gmail for new emails...")
            new_count = check_gmail(service, processed_ids)

            if new_count > 0:
                print(f"  -> {new_count} new important email(s) processed")
                logger.info(f"Processed {new_count} new important email(s)")
            else:
                print("  -> No new important emails")
                logger.info("No new important emails found")

            # Reset status counter after each check
            status_counter = 0

            # Wait with periodic ONLINE status updates
            for _ in range(CHECK_INTERVAL // STATUS_INTERVAL):
                time.sleep(STATUS_INTERVAL)
                status_counter += STATUS_INTERVAL
                if status_counter < CHECK_INTERVAL:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ONLINE - Monitoring Gmail...")
                    logger.info("ONLINE - Watcher is running")

    except KeyboardInterrupt:
        logger.info("Watcher stopped by user.")
        print("\n[INFO] Gmail watcher stopped by user.")

    logger.info("Watcher stopped.")
    print("[INFO] Gmail watcher stopped.")


if __name__ == "__main__":
    main()
