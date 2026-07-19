import sys, shutil, re
sys.stdout.reconfigure(encoding="utf-8") if sys.platform == "win32" else None
import logging
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PENDING_DIR = BASE / "Pending_Approval"
APPROVED_DIR = BASE / "Approved"
NEEDS_ACTION_DIR = BASE / "Needs_Action"

_COLORS = {"green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m", "blue": "\033[94m", "cyan": "\033[96m", "reset": "\033[0m"}

def c(text, color):
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"

def ok(msg):
    print(f"  {c('✓', 'green')} {c(msg, 'green')}")

def fail(msg):
    print(f"  {c('✗', 'red')} {c(msg, 'red')}")

def warn(msg):
    print(f"  {c('!', 'yellow')} {c(msg, 'yellow')}")

def _update_file_status(filepath: Path, new_status: str):
    content = filepath.read_text(encoding="utf-8")
    updated = re.sub(r'^status:.*$', f'status: {new_status}', content, flags=re.MULTILINE, count=1)
    filepath.write_text(updated, encoding="utf-8")

def list_pending():
    if not PENDING_DIR.exists():
        warn("Pending_Approval folder does not exist")
        return
    files = sorted(PENDING_DIR.glob("*.md"))
    if not files:
        warn("No pending items")
        return
    print(f"\n  {c('Pending Approval Items:', 'cyan')}\n")
    for f in files:
        content = f.read_text(encoding="utf-8")
        username = "?"
        message = "?"
        draft = "?"
        for line in content.split("\n"):
            if line.startswith("  username:"):
                username = line.split(":", 1)[1].strip().strip('"')
            elif line.startswith("message:"):
                message = line.split(":", 1)[1].strip().strip('"')
        if "## AI Draft Reply" in content:
            parts = content.split("## AI Draft Reply", 1)
            draft_section = parts[1].strip()
            draft = draft_section.split("\n")[0].strip() if draft_section else "?"
        print(f"  {c(f.name, 'blue')}")
        print(f"    From: @{username}")
        print(f"    Message: \"{message[:80]}{'...' if len(message) > 80 else ''}\"")
        print(f"    Draft: {draft[:80]}{'...' if len(draft) > 80 else ''}")
        print()

def approve(filename: str):
    src = PENDING_DIR / filename
    if not src.exists():
        fail(f"File not found: {filename}")
        return
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    _update_file_status(src, "approved")
    dest = APPROVED_DIR / filename
    shutil.move(str(src), str(dest))
    ok(f"{filename} → Approved/")
    print(f"  Run orchestrator to send: python orchestrator.py")

def reject(filename: str):
    src = PENDING_DIR / filename
    if not src.exists():
        fail(f"File not found: {filename}")
        return
    NEEDS_ACTION_DIR.mkdir(parents=True, exist_ok=True)
    _update_file_status(src, "needs_action")
    dest = NEEDS_ACTION_DIR / filename
    shutil.move(str(src), str(dest))
    ok(f"{filename} → Needs_Action/ (for revision)")

def show_help():
    print(f"\n  {c('Usage:', 'cyan')}")
    print(f"    python approve.py              — List pending items")
    print(f"    python approve.py list         — List pending items")
    print(f"    python approve.py approve <file> — Approve and move to Approved/")
    print(f"    python approve.py reject <file>  — Reject and move back to Needs_Action/")
    print()

if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "list":
        list_pending()
    elif sys.argv[1] == "approve" and len(sys.argv) >= 3:
        approve(sys.argv[2])
    elif sys.argv[1] == "reject" and len(sys.argv) >= 3:
        reject(sys.argv[2])
    else:
        show_help()
