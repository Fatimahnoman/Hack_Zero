# 🤖 Gold Tier — AI Employee

> Your personal AI assistant that handles Instagram DMs for you.

---

## ✨ What It Does

- 🔍 Monitors Instagram DMs 24/7
- 🧠 Detects important messages using keywords
- ✍️ Drafts professional replies automatically
- ✅ Sends only after YOUR approval

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/Fatimahnoman/Hack_Zero.git
cd Hack_Zero/Gold_Tier
pip install -r requirements.txt
playwright install chromium
```

### 2. Run Agent (Terminal 1)

```bash
python golden_tier_external_world/agent_instagram.py
```

> 🌐 Browser opens → Login once → Session saved forever

### 3. Run Orchestrator (Terminal 2)

```bash
python golden_tier_external_world/orchestrator.py
```

### 4. Approve Replies (Terminal 3)

```bash
python golden_tier_external_world/approve.py list
python golden_tier_external_world/approve.py approve <filename>
```

---

## 🔄 How It Works

```
📩 DM Received → 📥 Inbox → 📋 Plan Drafted → ✍️ Reply Ready → ✅ Approved → 🚀 Sent!
```

| Step | What Happens |
|------|--------------|
| 📩 DM Received | Agent detects new unread message |
| 📥 Inbox | Message saved as `.md` file |
| 📋 Plan Drafted | Orchestrator classifies intent + priority |
| ✍️ Reply Ready | AI drafts professional response |
| ✅ Approved | You review and approve |
| 🚀 Sent | DM delivered via Playwright |

---

## 🛠️ Tech Stack

| Tool | Purpose |
|------|---------|
| 🎭 Playwright | Browser automation |
| 🐍 Python | Core language |
| 📝 YAML | Data storage |
| 🔐 Session | Auto-login |

---

## 📁 What's Inside

| Folder | Purpose |
|--------|---------|
| 📥 Inbox/ | New DMs land here |
| 📋 Plan/ | Action plans |
| ✍️ Pending_Approval/ | Drafts awaiting your review |
| ✅ Approved/ | Ready to send |
| 🎉 Done/ | Successfully sent |
| 📊 Logs/ | Audit trail |

---

## 🔮 Coming Soon

- 💼 LinkedIn integration
- 🐦 Twitter integration
- 📘 Facebook integration
- 🤖 AI-powered replies (no templates!)

---

## 📌 Note

> First run requires manual Instagram login. After that, it's fully automatic!

---

*Built with ❤️ for autonomous customer communication*
