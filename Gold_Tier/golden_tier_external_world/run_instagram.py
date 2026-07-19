import sys, time, logging, os
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8") if sys.platform == "win32" else None
sys.stdout.flush()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logging.getLogger("EventBus").setLevel(logging.WARNING)
logging.getLogger("captcha").setLevel(logging.ERROR)
logging.getLogger("PlaywrightManager").setLevel(logging.WARNING)

os.environ["NODE_OPTIONS"] = "--no-deprecation"

from golden_tier_external_world.config.enums import PlatformType, EventType as Et
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.events.bus import LocalEventBus
from golden_tier_external_world.watchers.instagram import InstagramWatcher

_LOG = logging.getLogger("run_instagram")

BASE = Path(__file__).parent
SESSION_DIR = BASE / "session" / "instagram"
VAULT_DIR = BASE / "vault"
COOKIES_FILE = SESSION_DIR / "cookies_backup.json"


def _has_session_id():
    if not COOKIES_FILE.exists():
        return False
    import json
    try:
        cookies = json.loads(COOKIES_FILE.read_text())
        return any(c.get("name") == "sessionid" for c in cookies)
    except Exception:
        return False


def _print_status(step, status, detail=""):
    icon = {"ok": "\u2713", "fail": "\u2717", "wait": "\u23F3", "info": "\u2139\uFE0F", "arrow": "\u2192"}.get(status, "\u2022")
    line = f"  {icon} {step}"
    if detail:
        line += f"  {detail}"
    print(line)


def main():
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_DIR.mkdir(parents=True, exist_ok=True)

    has_session = _has_session_id()
    headless = has_session

    print()
    print("  \u250F" + "\u2501" * 50 + "\u2513")
    print("  \u2503  Instagram Watcher")
    print("  \u2503" + "\u2501" * 50 + "\u251B")
    print(f"  \u2503  Mode:     {'HEADLESS (auto)' if headless else 'MANUAL (login needed)'}")
    print(f"  \u2503  Session:  {'saved' if has_session else 'none (first time)'}")
    print("  \u2517" + "\u2501" * 50 + "\u251B")
    print()

    config = WatcherConfig(
        platform=PlatformType.INSTAGRAM,
        poll_interval_seconds=30,
        max_events_per_poll=20,
        enabled=True,
    )

    storage = JsonBackend(VAULT_DIR)
    bus = LocalEventBus()
    seen = SeenVault(JsonBackend(VAULT_DIR / "ig_seen.json", auto_create=True))

    watcher = InstagramWatcher(
        config,
        storage,
        bus,
        user_data_dir=str(SESSION_DIR),
        headless=headless,
        screenshot_dir=VAULT_DIR / "screenshots",
        seen_vault=seen,
    )

    bus.subscribe(Et.MESSAGE, lambda e: print(f"  \uD83D\uDCAC  DM from {getattr(e.sender,'display_name','unknown')}: \"{e.content.text[:200]}\"" if hasattr(e,'content') and e.content.text else f"  \uD83D\uDCAC  DM from {getattr(e.sender,'display_name','unknown')}"))
    bus.subscribe(Et.COMMENT, lambda e: print(f"  \uD83D\uDCAC  Comment from {e.author.display_name if hasattr(e,'author') else 'unknown'}"))
    bus.subscribe(Et.LIKE, lambda e: print(f"  \u2764\uFE0F  Like from {e.actor.display_name if hasattr(e,'actor') else 'unknown'}"))
    bus.subscribe(Et.FOLLOW, lambda e: print(f"  \uD83D\uDC64  Follow from {e.follower.display_name if hasattr(e,'follower') else 'unknown'}"))
    bus.subscribe(Et.MENTION, lambda e: print(f"  @  Mention from {e.mentioned_by.display_name if hasattr(e,'mentioned_by') else 'unknown'}"))

    print()
    _print_status("Launching browser...", "wait")
    ok = watcher.authenticate()
    if not ok:
        print(f"\n  {'\u2717'} Authentication failed")
        sys.exit(1)

    _print_status("Logged in", "ok")

    _print_status("Monitoring started", "info")
    print()

    try:
        cycle = 1
        while True:
            events = watcher.poll()
            if events:
                print(f"\n  [{cycle}] Found {len(events)} new event{'s' if len(events) != 1 else ''}:")
                for ev in events:
                    text = getattr(getattr(ev, 'content', None), 'text', '')
                    sender = getattr(getattr(ev, 'sender', None), 'display_name', '') or getattr(getattr(ev, 'actor', None), 'display_name', '') or ''
                    suffix = f" from {sender}: \"{text[:120]}\"" if text and sender else f" from {sender}" if sender else ""
                    print(f"       \u2022 [{ev.event_type.name}] {suffix or ev.event_id}")
            else:
                print(f"  [{cycle}] No new events", end="\r")
            cycle += 1
            time.sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        print(f"\n\n  {'\u2713'} Stopped by user")
        print(f"  Session saved, next run will be automatic.")
        print()


if __name__ == "__main__":
    main()
