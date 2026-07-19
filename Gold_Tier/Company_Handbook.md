# 📋 Company Handbook — Gold Tier

---
last_updated: 2026-07-19
review_frequency: daily
tier: gold
---

## Rules of Engagement

### General Rules
1. Always be polite, professional, and helpful in all communications
2. Auto-flag messages containing: urgent, payment, hack, issue, problem, help
3. Keep all data local-first (privacy focused)
4. Log every action in /Logs/audit.md for full traceability
5. Maintian single browser session across all operations

### Communication Rules
- Respond to detected messages within 60 seconds of detection
- Auto-draft replies using dynamic AI templates (context-aware)
- Each reply must be unique per sender (no copy-paste)
- Require manual approval before sending any DM
- Never auto-reply to new contacts without approval

### Instagram DM Handling Rules
- Monitor inbox every 5 seconds for new unread messages
- Extract full sender info (username, display name, platform ID)
- Save important messages to /Inbox/ with YAML frontmatter
- Generate action plan in /Plan/ folder for each new message
- Move processed items through pipeline: Inbox → Needs_Action → Pending_Approval → Approved → Done
- Typing must be visible (character-by-character with 60ms delay)
- Only mark as sent after verifying input cleared (message actually delivered)
- On send failure: keep in Approved/, reset tab, retry on next cycle

### File Handling Rules
- Process all files in their respective pipeline folders
- Update YAML `status:` field on every file move
- Maintain /Dashboard.md with real-time stats
- Archive completed items to /Done/
- Log all actions in /Logs/audit.md

### Decision Thresholds
| Action | Auto-Approve | Require Approval |
|--------|-------------|------------------|
| Detect messages | ✅ Yes | - |
| Extract message content | ✅ Yes | - |
| Save to Inbox | ✅ Yes | - |
| Generate action plan | ✅ Yes | - |
| Draft AI reply | ✅ Yes | - |
| Send reply | - | ❌ Always (Human-in-the-Loop) |
| File operations | Create, Read, Move | Delete |

### Performance Targets
- Message detection latency: < 10 seconds
- Draft generation: < 2 seconds per message
- End-to-end pipeline (inbox → done): < 5 minutes
- Browser uptime: 24/7 continuous

### Escalation Path
1. Message detected → Plan generated → Draft created
2. If HIGH priority → Immediate notification for approval
3. If send fails 3 times → Flag for manual intervention
4. Critical errors → Log full trace and pause pipeline

---
*Gold Tier AI Employee — Fully autonomous detection with human-approved execution.*
