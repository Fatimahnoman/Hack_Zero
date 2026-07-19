# Gold Tier — AI Employee

> A personal AI employee that monitors your Instagram DMs, detects important messages, drafts professional replies, and sends them — all with your manual approval.

---

## Overview

Gold Tier is a **human-in-the-loop automation system** for managing Instagram communications. It runs 24/7 on your machine, watches for incoming DMs, classifies urgency, drafts context-aware replies, and holds them for your approval before sending.

**Current status:** Instagram DM handling is fully operational. LinkedIn, Twitter, and Facebook integrations are framework-ready and will be enabled in future updates.

---

## Architecture

```
┌─────────────────────┐
│   agent_instagram    │  Monitors Instagram DMs via Playwright
│   (Watcher)          │  Detects unread messages + keywords
└─────────┬───────────┘
          │  Creates .md files in /Inbox/
          ▼
┌─────────────────────┐
│    orchestrator      │  Processes files through pipeline
│    (Pipeline)        │  Generates plans + AI draft replies
└─────────┬───────────┘
          │  Moves to /Pending_Approval/
          ▼
┌─────────────────────┐
│     approve.py       │  You review and approve/reject
│     (Human Gate)     │  Approved replies are sent via DM
└─────────────────────┘
```

---

## Features

### Instagram DM Monitoring
- Continuous DM badge detection (checks every 30-90 seconds)
- Unread message extraction with sender info
- Username resolution from chat header + Instagram API
- Keyword-based message filtering (configurable)

### Intelligent Pipeline
- **5-stage processing:** Inbox → Needs_Action → Pending_Approval → Approved → Done
- Automatic intent classification (Security, Problem, Support, Question, General)
- Priority assignment (HIGH / MEDIUM / LOW)
- Action plan generation for each message

### Draft Reply Generation
- Template-based contextual replies per intent
- Unique responses per sender (no copy-paste)
- Priority-aware messaging (urgent messages get immediate language)
- Professional tone with acknowledgment + action + closing

### Manual Approval
- Human reviews every draft before sending
- Approve or reject from command line
- Full audit trail for every action

### Monitoring & Logging
- Real-time dashboard (`Dashboard.md`)
- Complete audit log (`Logs/audit.md`)
- File-level status tracking via YAML frontmatter

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Browser Automation | Playwright (Chromium) |
| Language | Python 3.10+ |
| Captcha Solving | 2Captcha API |
| Storage | JSON files + YAML frontmatter |
| Session Management | Persistent Chrome profile |
| Event System | Custom pub/sub bus with priority queue |

---

## Directory Structure

```
Gold_Tier/
├── Dashboard.md                  # Real-time stats dashboard
├── Company_Handbook.md           # Rules of engagement
├── README.md                     # This file
│
├── Inbox/                        # New DM files land here
├── Needs_Action/                 # Plans generated, awaiting draft
├── Pending_Approval/             # Draft replies ready for review
├── Approved/                     # Approved, ready to send
├── Done/                         # Successfully sent replies
├── Plan/                         # Generated action plans
├── Logs/                         # Audit trail
│   └── audit.md
├── vault/                        # Persistent state storage
├── session/                      # Browser session data
│
└── golden_tier_external_world/   # Core application code
    ├── agent_instagram.py        # Instagram DM watcher/agent
    ├── orchestrator.py           # Pipeline engine
    ├── approve.py                # Manual approval CLI
    ├── run_instagram.py          # Modular watcher runner
    ├── run_instagram_watcher.py  # Watcher with captcha support
    │
    ├── browser/                  # Playwright browser manager
    ├── config/                   # Settings, secrets, enums
    ├── events/                   # Event bus + models
    ├── watchers/                 # Platform-specific watchers
    │   ├── instagram/
    │   ├── linkedin/
    │   ├── twitter/
    │   └── facebook/
    ├── posters/                  # Platform-specific posters
    ├── content_orchestrator/     # Content generation engine
    ├── storage/                  # JSON/SQLite backends
    ├── models/                   # Data models
    ├── monitoring/               # Health checks + metrics
    ├── utils/                    # Captcha solver, helpers
    └── tests/                    # Unit + integration tests
```

---

## Getting Started

### Prerequisites
- Python 3.10+
- Playwright (`pip install playwright && playwright install chromium`)
- Instagram account (for manual login on first run)

### 1. Run the Instagram Agent

```bash
python golden_tier_external_world/agent_instagram.py
```

- Opens a browser window — log in to Instagram manually on first run
- Session is saved for future runs
- Monitors DM badge and processes unread messages
- Creates `.md` files in `Inbox/` with message details

### 2. Run the Orchestrator

```bash
python golden_tier_external_world/orchestrator.py
```

- Processes files from `Inbox/` through the pipeline
- Generates action plans and draft replies
- Moves files to `Pending_Approval/` for your review

### 3. Approve or Reject Drafts

```bash
# List pending items
python golden_tier_external_world/approve.py list

# Approve a specific file
python golden_tier_external_world/approve.py approve <filename>

# Reject and send back
python golden_tier_external_world/approve.py reject <filename>
```

Approved files are sent as Instagram DMs and moved to `Done/`.

---

## Pipeline Flow

```
[Instagram DM Received]
        │
        ▼
   ┌─────────┐     Agent detects unread message
   │  Inbox   │     Creates .md with YAML frontmatter
   └────┬────┘
        │
        ▼
   ┌──────────────┐  Orchestrator generates action plan
   │ Needs_Action  │  Classifies intent + priority
   └────┬─────────┘
        │
        ▼
   ┌──────────────────┐  AI draft reply generated
   │ Pending_Approval  │  Waiting for human review
   └────┬─────────────┘
        │
        ▼
   ┌──────────┐  You approve via approve.py
   │ Approved  │  Agent sends DM via Playwright
   └────┬─────┘
        │
        ▼
   ┌──────┐  Moved here after successful send
   │ Done  │  Full audit log maintained
   └──────┘
```

---

## Configuration

### Keywords (in agent code)
Messages containing these words are flagged for processing:
```
urgent, emergency, need help, asap, problem, issue, critical,
broken, error, fix, help, important, hack, password, reset
```

### Polling Intervals
| Setting | Default |
|---------|---------|
| DM badge check | 30-90 seconds (randomized) |
| Orchestrator loop | 5 seconds |
| Post-send cooldown | 2 seconds |

### Session Management
- Browser profile saved to `golden_tier_external_world/session/instagram/`
- Cookies backed up to `cookies_backup.json`
- First run requires manual login; subsequent runs are automatic

---

## Message Format

Each detected DM is saved as a Markdown file with YAML frontmatter:

```markdown
---
type: instagram
from: "Display Name"
username: "handle"
thread_id: "17846275886515207"
received_at: "2026-07-19 8:49 PM"
priority: high
status: pending
action_type: dm
keywords_detected: true
---

## Instagram Activity Detected

**From:** Display Name (@handle)
**Type:** DM
**Message:** Will you do collaboration with me? It's urgent

**Detected At:** 2026-07-19 8:49 PM

## AI Draft Reply

Hi Display Name!

I understand this is urgent, so I'm prioritizing it immediately.
Thank you for reaching out regarding collaboration. I'll take care
of this for you.

I'll update you as soon as I have more information. Appreciate your patience!
```

---

## Future Enhancements

| Platform | Status | Notes |
|----------|--------|-------|
| Instagram | Live | DM monitoring + reply sending |
| LinkedIn | Framework ready | Watcher + poster stubs exist |
| Twitter | Framework ready | Watcher + poster stubs exist |
| Facebook | Framework ready | Watcher + poster stubs exist |

### Planned Features
- LLM-powered draft replies (replace templates with AI generation)
- Retry logic with exponential backoff for failed sends
- Log rotation for audit files
- Multi-account support
- Webhook notifications for HIGH priority messages
- Browser health monitoring + auto-restart

---

## License

Internal project — not for distribution.

---

*Gold Tier AI Employee v0.1 — Autonomous detection, human-approved execution.*
