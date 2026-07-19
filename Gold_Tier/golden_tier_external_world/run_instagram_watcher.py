"""
Instagram Watcher Runner — Auto-login with captcha solving or manual login fallback.

Usage:
    python run_instagram_watcher.py --once                             # first time: manual login, session saved
    python run_instagram_watcher.py --once --headless                  # after session saved: fully automatic
    python run_instagram_watcher.py --once --email user --pass pass    # auto-login with credentials
    python run_instagram_watcher.py --once --headless --email user --pass pass  # fully headless auto-login
"""

import sys, os, argparse, time, logging
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8") if sys.platform == "win32" else None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golden_tier_external_world.config.secrets import load_secrets, get_secret
from golden_tier_external_world.config.enums import PlatformType, EventType as Et
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.interface import StorageInterface
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.events.bus import LocalEventBus
from golden_tier_external_world.watchers.instagram import InstagramWatcher
from golden_tier_external_world.utils.captcha import ReCaptchaSolver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="Run Instagram Watcher")
    parser.add_argument(
        "--session",
        default=r"C:\Users\LENOVO\Desktop\Hackathon_0\AI_Employee_Vault\Gold_Tier\golden_tier_external_world\session\instagram",
        help="Path to Chrome user-data-dir",
    )
    parser.add_argument("--headless", action="store_true", help="Run headless")
    parser.add_argument(
        "--once", action="store_true", help="Single poll cycle, then exit"
    )
    parser.add_argument(
        "--poll-interval", type=int, default=30, help="Poll interval seconds"
    )
    parser.add_argument(
        "--vault",
        default=r"C:\Users\LENOVO\Desktop\Hackathon_0\AI_Employee_Vault\Gold_Tier\vault",
        help="Vault path for storage",
    )
    parser.add_argument(
        "--env",
        default=r"C:\Users\LENOVO\Desktop\Hackathon_0\AI_Employee_Vault\Gold_Tier\golden_tier_external_world\.env",
        help="Path to .env file with credentials (optional)",
    )
    parser.add_argument("--email", default=None, help="Instagram email/username")
    parser.add_argument("--password", default=None, help="Instagram password")
    parser.add_argument(
        "--captcha-key", default=None, help="2Captcha API key (optional)"
    )
    args = parser.parse_args()

    load_secrets(Path(args.env), override_environ=True)

    vault_path = Path(args.vault)
    vault_path.mkdir(parents=True, exist_ok=True)

    config = WatcherConfig(
        platform=PlatformType.INSTAGRAM,
        poll_interval_seconds=args.poll_interval,
        max_events_per_poll=20,
        enabled=True,
    )

    storage = JsonBackend(vault_path)
    bus = LocalEventBus()
    seen = SeenVault(JsonBackend(vault_path / "ig_seen.json", auto_create=True))

    session_path = Path(args.session)
    session_path.mkdir(parents=True, exist_ok=True)
    cookies_file = session_path / "cookies_backup.json"

    print(f"Session path: {session_path}")
    print(f"Cookies file: {'EXISTS' if cookies_file.exists() else 'NOT FOUND'}")

    email = args.email or get_secret("INSTAGRAM_EMAIL")
    password = args.password or get_secret("INSTAGRAM_PASSWORD")
    captcha_key = args.captcha_key or get_secret("CAPTCHA_API_KEY")

    if email or password or captcha_key:
        print(f"Email: {'✓ set' if email else '✗ not set'}")
        print(f"Password: {'✓ set' if password else '✗ not set'}")
        print(f"Captcha Key: {'✓ set' if captcha_key else '✗ not set'}")

    captcha_solver = ReCaptchaSolver(api_key=captcha_key)

    watcher = InstagramWatcher(
        config,
        storage,
        bus,
        user_data_dir=session_path,
        headless=args.headless,
        screenshot_dir=vault_path / "screenshots",
        seen_vault=seen,
        username=email,
        password=password,
        captcha_solver=captcha_solver,
    )

    def log_event(event):
        name = (
            getattr(event, "sender", None)
            or getattr(event, "author", None)
            or getattr(event, "actor", None)
            or getattr(event, "follower", None)
        )
        print(f"  EVENT: [{event.event_type.name}] {name}")

    for et in Et:
        bus.subscribe(et, log_event)

    if args.once:
        print("🔍 Single poll mode...")
        ok = watcher.authenticate()
        if ok:
            print("✅ Authenticated!")
            events = watcher.poll()
            print(f"\n📊 Found {len(events)} events:")
            for ev in events:
                print(f"  - {ev.event_id}: {ev.event_type.name}")
        else:
            print("❌ Authentication failed")
        return

    print(f"🚀 Starting watcher (poll every {args.poll_interval}s)...")
    try:
        watcher.start()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        watcher.stop()


if __name__ == "__main__":
    main()
