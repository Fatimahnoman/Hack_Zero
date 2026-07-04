# 🥉 Bronze Tier - Foundation

## Quick Setup (After Cloning)
Bronze Tier uses only Python standard library — no extra dependencies needed.
```bash
# Just run the orchestrator
cd Bronze_Tier/scripts
python orchestrator.py --continuous
```

## Overview
This is the Bronze Tier implementation of the Personal AI Employee.

## What's Included
- ✅ Obsidian vault structure (using VS Code)
- ✅ Dashboard.md for real-time summary
- ✅ Company_Handbook.md with rules of engagement
- ✅ Basic folder structure: Inbox, Needs_Action, Done, Plans, Logs
- ✅ One working Watcher script (File System Watcher)
- ✅ Claude Code integration (read/write to vault)

## Folder Structure
```
Bronze_Tier/
├── Dashboard.md           # Real-time summary
├── Company_Handbook.md    # Rules of engagement
├── Inbox/                 # Manual input folder
├── Needs_Action/          # Watcher drops files here
├── Done/                  # Completed tasks
├── Plans/                 # Claude creates plans here
└── Logs/                  # Action audit logs
```

## How to Use

### Option 1: Orchestrator (Recommended) ✨
The Orchestrator manages the complete workflow automatically:

**Continuous Mode (Runs forever):**
```bash
python scripts/orchestrator.py --continuous
```
Or double-click: `start_orchestrator.bat`

**Manual Mode (Run once):**
```bash
python scripts/orchestrator.py
```
Or double-click: `test_workflow.bat`

### Option 2: Individual Scripts

**1. Start the File System Watcher**
```bash
python scripts/filesystem_watcher.py
```

**2. Drop a file in the Inbox folder**
The watcher will detect it and create an action file in `Needs_Action/`.

**3. Ask Claude Code to process**
```bash
claude "Check the Needs_Action folder and create a plan for each item"
```

**4. Review and execute**
Claude will suggest actions. In Bronze tier, you manually execute them.

### Workflow Example

**Single File Movement Flow:**

```
Start: sample_email.txt in Inbox/
  ↓
[Step 1] Orchestrator detects file
  ↓
Move: sample_email.txt → Needs_Action/
  ↓
Inbox: ✅ EMPTY
  ↓
[Step 2] Orchestrator processes file
  ↓
Create: PLAN_sample_email.md in Plans/ (PERMANENT)
  ↓
Move: sample_email.txt → Done/
  ↓
Needs_Action: ✅ EMPTY
  ↓
Done: sample_email.txt (same original file)
```

**Final Result:**
- ✅ **Inbox:** Empty (ready for new files)
- ✅ **Needs_Action:** Empty (ready for new tasks)
- ✅ **Plans:** Has PLAN_sample_email.md (permanent reference)
- ✅ **Done:** Has sample_email.txt (same original file, only 1 file)

**Key Point:** Only ONE file moves through the entire workflow - no duplicates!

## Next Steps
- Upgrade to Silver Tier for multiple watchers + MCP servers
- Add automated approval workflow
- Add scheduling via cron/Task Scheduler

## Security Notes
- Never commit `.env` files
- Keep credentials out of vault
- Review logs regularly
