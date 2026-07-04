"""
Fix Gmail Authentication - Add gmail.send scope
=================================================
This script re-authenticates with Gmail SEND permissions.
"""

import sys
from pathlib import Path

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

VAULT_PATH = Path(__file__).parent.parent
TOKEN_FILE = VAULT_PATH / "token.json"
CREDENTIALS_FILE = VAULT_PATH / "credentials.json"

# Required scopes for sending emails
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly"
]

def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ Google libraries not installed!")
        print("\n💡 Install them with:")
        print("   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        return

    print("\n" + "=" * 60)
    print("🔐 Gmail Authentication Fix - Silver Tier")
    print("=" * 60)
    print()
    print("⚠️  Current token has ONLY read-only permissions")
    print("✅ This will re-authenticate with SEND permissions")
    print()

    if not CREDENTIALS_FILE.exists():
        print(f"❌ Credentials file not found: {CREDENTIALS_FILE}")
        return

    print("📋 Required scopes:")
    for scope in SCOPES:
        print(f"   ✓ {scope}")
    print()

    try:
        print("🌐 Opening browser for authentication...")
        print("   (Please allow permissions when prompted)")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(
            CREDENTIALS_FILE,
            SCOPES
        )

        creds = flow.run_local_server(port=0)

        print("\n✅ Authentication successful!")
        print(f"   Token saved to: {TOKEN_FILE}")

        # Save new token
        TOKEN_FILE.write_text(creds.to_json())

        print()
        print("=" * 60)
        print("✅ DONE! Gmail SEND permission granted!")
        print("=" * 60)
        print()
        print("🚀 You can now run orchestrator.py")
        print("   Emails will actually send now!")
        print()

    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        print()
        print("💡 Try installing dependencies first:")
        print("   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client")

if __name__ == "__main__":
    main()
