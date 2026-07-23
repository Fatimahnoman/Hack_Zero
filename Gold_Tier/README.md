# 🤖 Gold Tier — AI Employee

> Your personal AI assistant that handles Instagram DMs, Likes & Mentions for you.

---

## ✨ What It Does

- 🔍 Monitors Instagram DMs 24/7
- ❤️ Detects new likes on your posts 24/7
- @️ Detects new mentions on your posts 24/7
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

### 2. Run Agent (DM + Like + Mention Watcher)

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

### DM Pipeline

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

### Like Watcher

```
❤️ Someone likes your post → 📁 Logged to Likes/ folder
```

| Step | What Happens |
|------|--------------|
| ❤️ Like Detected | Agent checks notifications every 3rd cycle |
| 📁 Logged | Like saved as `.md` file with username + post URL |
| 🔄 Dedup | Same like never logged twice |

### Mention Watcher

```
@️ Someone mentions you → 📥 Saved to Inbox/ → ✍️ AI generates Thank You Reply
```

| Step | What Happens |
|------|--------------|
| @️ Mention Detected | Agent checks notifications every cycle |
| 📥 Saved | Mention saved to `Inbox/` with username + caption |
| ✍️ AI Reply | Orchestrator generates a Thank You reply |
| ✅ Approved | You review and approve before sending |

---

## 🛠️ Tech Stack

| Tool | Purpose |
|------|---------|
| 🎭 Playwright | Browser automation |
| 🐍 Python | Core language |
| 📝 Markdown | Data storage |
| 🔐 Session | Auto-login |

---

## 📁 What's Inside

| Folder | Purpose |
|--------|---------|
| 📥 Inbox/ | New DMs + Mentions land here |
| ❤️ Likes/ | Like events logged here |
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
