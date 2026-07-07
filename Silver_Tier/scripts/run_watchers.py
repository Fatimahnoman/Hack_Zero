"""
Combined Launcher - Runs WhatsApp + Gmail watchers together
========================================================
Usage:
  python scripts/run_watchers.py
"""

import subprocess
import sys
import threading
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
WHATSAPP_WATCHER = str(SCRIPTS_DIR / "whatsapp_watcher.py")
GMAIL_WATCHER = str(SCRIPTS_DIR / "gmail_watcher.py")


def stream_output(name, proc):
    for line in iter(proc.stdout.readline, ''):
        if line:
            print(f"[{name}] {line}", end='', flush=True)


def main():
    print("=" * 60)
    print("AI Employee - Combined Watchers Launcher")
    print("=" * 60)
    print(f"  WhatsApp: {WHATSAPP_WATCHER}")
    print(f"  Gmail:    {GMAIL_WATCHER}")
    print("=" * 60)
    print()

    processes = []

    try:
        for label, script in [("WhatsApp", WHATSAPP_WATCHER), ("Gmail", GMAIL_WATCHER)]:
            print(f"[LAUNCH] Starting {label} watcher...")
            proc = subprocess.Popen(
                [sys.executable, "-u", script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            processes.append((label, proc))
            t = threading.Thread(target=stream_output, args=(label, proc), daemon=True)
            t.start()

        print("\nBoth watchers running. Press Ctrl+C to stop both.\n")

        while all(p.poll() is None for _, p in processes):
            try:
                for _, p in processes:
                    p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue

    except KeyboardInterrupt:
        print("\n\n[STOP] Shutting down watchers...")
    finally:
        for name, proc in processes:
            if proc.poll() is None:
                print(f"  Stopping {name} watcher...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        print("Both watchers stopped.")


if __name__ == "__main__":
    main()
