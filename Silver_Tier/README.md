# 🥈 Silver Tier - Functional Assistant

## Quick Setup (After Cloning)
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browser
playwright install chromium

# 3. Authenticate Gmail (opens browser - login with YOUR Google account)
python scripts/gmail_watcher.py

# 4. WhatsApp session (first time - scan QR with phone)
python scripts/whatsapp_watcher.py
```

## Overview
Silver Tier builds on Bronze with multiple watchers, approval workflow, and enhanced automation.

## What's Included
- ✅ Complete vault structure (Inbox, Needs_Action, Plans, Done, etc.)
- ✅ **2 Watchers**: Gmail + WhatsApp (simulated for testing)
- ✅ **Approval Workflow**: Human-in-the-Loop (HITL) system
- ✅ Enhanced Orchestrator with continuous monitoring
- ✅ Single file movement workflow
- ✅ Dashboard with real-time stats

## Folder Structure
```
Silver_Tier/
├── Dashboard.md                 # Real-time summary
├── Company_Handbook.md          # Rules of engagement
├── Inbox/                       # New files/emails/messages
├── Needs_Action/                # Files being processed
├── Pending_Approval/            # Awaiting human approval
├── Approved/                    # Approved actions
├── Plans/                       # Generated plans (permanent)
├── Done/                        # Completed tasks
├── Logs/                        # Audit logs
└── scripts/
    ├── orchestrator.py          # Main workflow manager
    ├── gmail_watcher.py         # Gmail monitor
    ├── whatsapp_watcher.py      # WhatsApp monitor
    ├── vault_manager.py         # Vault helper
    └── approval_handler.py      # HITL approval system
```

## How to Use

### 1. Run Orchestrator (Continuous Mode)
```bash
cd scripts
python orchestrator.py --continuous
```

### 2. Drop File in Inbox
- Any file dropped in Inbox will be automatically processed
- File moves: Inbox → Needs_Action → Plans + Done

### 3. Approval Workflow
- When action requires approval, file appears in `Pending_Approval/`
- **To Approve:** Move file to `Approved/` folder
- **To Reject:** Delete the file or move to `Rejected/` folder

### 4. Check Dashboard
```bash
type Dashboard.md
```

## Single File Workflow

```
Start: file.md in Inbox/
  ↓
[Step 1] Orchestrator detects
  ↓
Move: file.md → Needs_Action/
  ↓ (Inbox empty)
[Step 2] Orchestrator processes
  ↓
Create: PLAN_file.md in Plans/ (PERMANENT)
Create: Approval in Pending_Approval/
  ↓
Move: file.md → Done/
  ↓ (Needs_Action empty)
[Step 3] Human approves
  ↓
Move: Approval → Approved/ → Done/
  ↓
✅ Complete!
```

## Watchers

### Gmail Watcher
- Checks every 2 minutes
- Creates email files in Inbox
- Simulated for testing (real API needs setup)

### WhatsApp Watcher
- Checks every 30 seconds
- Monitors keywords: urgent, asap, invoice, payment, help
- Creates message files in Inbox
- **Session Management**: First time QR scan, then auto-login
- **Browser Modes**:
  - **Visible Mode** (default): `python whatsapp_watcher.py`
    - ✅ Browser window open hota hai
    - ✅ Aap dekh sakti ho AI kya kar raha hai
    - ✅ Typing, clicking, sending - SAB DIKHAI DEGHA
  - **Headless Mode**: `python whatsapp_watcher.py --headless`
    - ❌ Browser invisible hota hai
    - ✅ Fast, server pe chal sakta hai

### Session Persistence
- **First Time**: WhatsApp Web shows QR code → scan with phone
- **Session Saved**: `sessions/whatsapp/` folder mein
- **Next Time**: Auto-login, no QR scan needed
- **Session Expires**: Re-scan if logged out from phone

## Test Commands

```bash
# Run orchestrator once
python orchestrator.py

# Run continuously
python orchestrator.py --continuous

# Check folders
dir ..\Inbox
dir ..\Needs_Action
dir ..\Pending_Approval
dir ..\Plans
dir ..\Done
```

## Next Steps
- Upgrade to Gold Tier for full cross-domain integration
- Add MCP servers for external actions
- Add scheduling via cron/Task Scheduler
- Implement real Gmail/WhatsApp integration

## Security Notes
- Never commit `.env` files
- Keep credentials out of vault
- Review approval requests carefully
- Check logs regularly
