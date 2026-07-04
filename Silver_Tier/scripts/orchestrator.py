"""
Orchestrator - Silver Tier (Complete Workflow)
===============================================
Workflow:
1. Watcher detects → Inbox
2. Orchestrator: Inbox → Needs_Action → Plans (PERMANENT) → Pending_Approval
3. Human approves → moves to Approved/
4. Orchestrator: Approved → MCP Send → Done

Commands:
  python orchestrator.py              # Continuous mode (24/7)
"""

import time
import logging
import sys
from pathlib import Path
from datetime import datetime
from vault_manager import VaultManager

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


class Orchestrator:
    def __init__(self, vault_path: str, check_interval: int = 5):
        self.vault_path = Path(vault_path)
        self.inbox = self.vault_path / 'Inbox'
        self.needs_action = self.vault_path / 'Needs_Action'
        self.pending_approval = self.vault_path / 'Pending_Approval'
        self.approved = self.vault_path / 'Approved'
        self.plans = self.vault_path / 'Plans'
        self.done = self.vault_path / 'Done'
        self.logs = self.vault_path / 'Logs'
        self.check_interval = check_interval

        self.vault_manager = VaultManager(vault_path=str(vault_path))

        # Setup logging
        log_file = self.logs / f'orchestrator_{datetime.now().strftime("%Y%m%d")}.log'
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.stream = open(sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False)

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[file_handler, stream_handler]
        )
        self.logger = logging.getLogger('Orchestrator')

        # Ensure folders exist
        for folder in [self.inbox, self.needs_action, self.pending_approval,
                       self.approved, self.plans, self.done, self.logs]:
            folder.mkdir(parents=True, exist_ok=True)

    def process_inbox_to_pending(self):
        """Inbox → Needs_Action → Plans (PERMANENT) → Pending_Approval"""
        processed = 0

        # Step 1: Inbox → Needs_Action
        for file in list(self.inbox.iterdir()):
            if file.is_file() and not file.name.startswith('.'):
                print(f'\n📥 Detected: {file.name}')
                print(f'   📂 Moving: Inbox → Needs_Action...')
                dest = self.needs_action / file.name
                file.rename(dest)
                print(f'   ✅ Moved')
                processed += 1

        # Step 2: Needs_Action → Plans + Pending_Approval
        for file in list(self.needs_action.iterdir()):
            if file.is_file() and not file.name.startswith('.'):
                print(f'\n🧠 Processing: {file.name}')

                # Read content
                content = file.read_text(encoding='utf-8')
                task_name = file.stem
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

                # Generate reply
                reply_draft = self._generate_reply_draft(content)

                # Create PERMANENT plan (stays in Plans/ forever)
                plan_filename = f'PLAN_{task_name}_{timestamp}.md'
                plan_path = self.plans / plan_filename
                plan_content = f"""---
type: plan
created: {datetime.now().isoformat()}
status: active
source_file: {file.name}
priority: medium
tier: silver
---

# 📋 Plan: {task_name.replace('_', ' ').title()}

## Source File
Original file: {file.name}

## File Content
{content}

## AI-Generated Reply Draft
{reply_draft}

## Workflow Steps
- [x] Step 1: Detect message
- [x] Step 2: Create plan (PERMANENT - stays in Plans/)
- [x] Step 3: Generate reply draft
- [ ] Step 4: Human approval (file in Pending_Approval)
- [ ] Step 5: Send via MCP (after approval)
- [ ] Step 6: Move to Done (after successful send)

---
*Created by AI Employee Silver Tier - Plan is permanent*
"""
                plan_path.write_text(plan_content, encoding='utf-8')
                print(f'   ✅ Plan created (PERMANENT): {plan_filename}')

                # Move SAME file to Pending_Approval with draft reply added
                updated_content = content.rstrip() + f"""

---

## 🔐 PENDING APPROVAL

**Status:** Waiting for human approval  
**Created:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## 💬 AI-Generated Draft Reply

{reply_draft}

---

## ⚠️ ACTION REQUIRED

**To Approve:** Move this file to `Approved/` folder

**After Approval:** Orchestrator will automatically send via MCP

---
*Moved to Pending_Approval - Waiting for human action*
"""
                dest = self.pending_approval / file.name
                dest.write_text(updated_content, encoding='utf-8')
                file.unlink()  # Remove from Needs_Action

                self.vault_manager.log_action(
                    'inbox_to_pending',
                    {'file': file.name, 'plan': plan_filename},
                    'success'
                )

                print(f'   ✅ Same file moved to Pending_Approval')
                print(f'   🔐 Draft reply added to file')
                print(f'   ⏳ WAITING for human approval...')
                processed += 1

        return processed

    def process_approved_files(self):
        """Approved → MCP Send → Done (Auto-detect WhatsApp vs Gmail)"""
        processed = 0

        for file in list(self.approved.iterdir()):
            if file.is_file() and file.suffix == '.md':
                print(f'\n✅ Processing approved: {file.name}')

                content = file.read_text(encoding='utf-8')
                
                # Extract reply
                reply_draft = self._extract_reply(content)

                # Detect Type
                is_whatsapp = "type: whatsapp_message" in content
                is_email = "type: email" in content

                print(f'   📤 Sending...')

                success = False
                if is_whatsapp:
                    contact_name = self._extract_contact_name(file.name)
                    print(f'   Type: WhatsApp -> {contact_name}')
                    print(f'   Message: {reply_draft[:80]}...')
                    success = self._send_via_whatsapp(contact_name, reply_draft)
                elif is_email:
                    # Extract recipient from full file content (NOT just reply draft)
                    recipient_email = self._extract_recipient_email(content)
                    print(f'   Type: Gmail -> {recipient_email}')
                    print(f'   Message: {reply_draft[:80]}...')
                    success = self._send_via_gmail(recipient_email, reply_draft)
                else:
                    print(f'   ⚠️  Unknown type. Skipping.')

                if success:
                    # Update file
                    updated = content.replace(
                        '**Status:** Waiting for human approval',
                        f'**Status:** ✅ Sent on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                    )

                    # Move to Done
                    dest = self.done / file.name
                    dest.write_text(updated, encoding='utf-8')
                    file.unlink()

                    print(f'   ✅ Sent successfully! Moved to Done/')
                    processed += 1
                else:
                    print(f'   ❌ Send FAILED - file stays in Approved/')
                    print(f'   ⚠️  Manual intervention may be required')
                    # Update file to mark as failed
                    failed_content = content.replace(
                        '**Status:** Waiting for human approval',
                        f'**Status:** ❌ Send FAILED on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} - REQUIRES ACTION'
                    )
                    file.write_text(failed_content, encoding='utf-8')

        return processed

    def _send_via_whatsapp(self, contact_name: str, message: str) -> bool:
        """Send via WhatsApp Sender Agent Skill"""
        try:
            skill_path = Path(__file__).parent.parent / 'skills' / 'whatsapp_sender.py'
            if not skill_path.exists():
                print(f'   ⚠️  WhatsApp Skill not found')
                return False

            import subprocess
            cmd = [
                sys.executable,
                str(skill_path),
                '--to', contact_name,
                '--message', message
            ]

            result = subprocess.run(cmd, timeout=120)
            return result.returncode == 0

        except Exception as e:
            self.logger.error(f'WhatsApp send failed: {e}')
            return False

    def _send_via_gmail(self, recipient_email: str, message: str) -> bool:
        """Send via Gmail Sender Agent Skill with proper error checking"""
        try:
            skill_path = Path(__file__).parent.parent / 'skills' / 'gmail_sender.py'
            if not skill_path.exists():
                print(f'   ❌ Gmail Skill not found')
                return False

            import subprocess
            
            print(f'   📧 Using Gmail Skill to send to: {recipient_email}')
            
            cmd = [
                sys.executable,
                str(skill_path),
                '--to', recipient_email,
                '--message', message
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            # Check if the subprocess actually succeeded (exit code 0)
            if result.returncode == 0:
                print(f'   ✅ Gmail sender confirmed success')
                if result.stdout:
                    for line in result.stdout.split('\n'):
                        if line.strip():
                            print(f'      {line}')
                return True
            else:
                print(f'   ❌ Gmail sender failed (exit code: {result.returncode})')
                if result.stderr:
                    print(f'      Error: {result.stderr}')
                if result.stdout:
                    print(f'      Output: {result.stdout}')
                return False

        except subprocess.TimeoutExpired:
            print(f'   ❌ Gmail sender timed out (60s limit)')
            return False
        except Exception as e:
            self.logger.error(f'Gmail send failed: {e}')
            print(f'   ❌ Exception during Gmail send: {e}')
            return False

    def _extract_recipient_email(self, content: str) -> str:
        """Extract recipient email from approved file content"""
        try:
            # First, look for 'from:' field in YAML frontmatter
            for line in content.split('\n'):
                if line.lower().startswith('from:'):
                    # Extract email from angle brackets if present
                    from_value = line.split(':', 1)[1].strip()
                    import re
                    angle_email = re.search(r'<(.+?)>', from_value)
                    if angle_email:
                        return angle_email.group(1)
                    # Otherwise look for direct email
                    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
                    matches = re.findall(email_pattern, from_value)
                    if matches:
                        return matches[0]
            
            # Fallback: search entire content for email pattern
            import re
            email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
            matches = re.findall(email_pattern, content)
            if matches:
                return matches[0]
            
            # Final fallback
            return 'user@example.com'
        except Exception as e:
            print(f'   ⚠️  Warning: Could not extract email: {e}')
            return 'user@example.com'

    def _generate_reply_draft(self, content: str) -> str:
        """Generate intelligent reply"""
        content_lower = content.lower()
        if 'urgent' in content_lower or 'asap' in content_lower:
            return "Hi! Thank you for your message. I understand this is urgent and I'm prioritizing it right now. Let me review the details and get back to you very shortly. Thank you for your patience! 🙏"
        elif 'invoice' in content_lower:
            return "Hi! Thank you for reaching out about the invoice. I'll prepare it right away and send it to you as soon as possible. Please share any additional details if needed. Thanks! 📄"
        elif 'payment' in content_lower:
            return "Hi! I've received your message regarding payment. Let me check the records and get back to you shortly. Thank you! 💰"
        elif 'price' in content_lower or 'quote' in content_lower:
            return "Hi! Thanks for your interest in our services. I'll prepare a detailed quote for you and send it over soon. Could you share any specific requirements? Thanks! 💼"
        else:
            return "Hi! Thank you for your message. I've received it and will respond as soon as possible. Thanks for reaching out! 😊"

    def _extract_contact_name(self, filename: str) -> str:
        """Extract contact from filename like WHATSAPP_Contact_Name_timestamp.md"""
        try:
            stem = Path(filename).stem
            parts = stem.split('_')
            if len(parts) >= 3:
                return ' '.join(parts[1:-2]).replace('_', ' ')
            return 'Unknown Contact'
        except:
            return 'Unknown Contact'

    def _extract_reply(self, content: str) -> str:
        """Extract reply draft from file"""
        try:
            start = content.find('## 💬 AI-Generated Draft Reply')
            if start == -1:
                return ''
            start += len('## 💬 AI-Generated Draft Reply')
            remaining = content[start:]
            end = remaining.find('---')
            if end == -1:
                return remaining.strip()
            return remaining[:end].strip()
        except:
            return ''

    def run_continuous(self):
        """Main loop - 24/7"""
        print('\n' + '=' * 60)
        print('🤖 AI Employee Orchestrator - Silver Tier')
        print('=' * 60)
        print(f'📂 Vault: {self.vault_path}')
        print(f'⏱️  Check interval: {self.check_interval}s')
        print(f'🔄 Mode: CONTINUOUS (24/7)')
        print(f'🛑 Stop: Press Ctrl+C')
        print('=' * 60)
        print()
        print('📋 Workflow:')
        print('   Inbox → Needs_Action → Plans (PERMANENT) → Pending_Approval')
        print('   Human approves → Approved → MCP Send → Done')
        print()
        print('👀 Watching folders...')
        print()

        cycle_count = 0

        try:
            while True:
                cycle_count += 1

                # Process Inbox → Pending_Approval
                inbox_done = self.process_inbox_to_pending()

                # Process Approved → Done
                approved_done = self.process_approved_files()

                # Status every 5 cycles
                if cycle_count % 5 == 0:
                    stats = self.vault_manager.update_dashboard()
                    print(f'\n💓 Heartbeat - Cycle #{cycle_count}')
                    print(f'   📥 Inbox: {stats["inbox"]}')
                    print(f'   📋 Needs_Action: {stats["needs_action"]}')
                    print(f'   🔐 Pending_Approval: {stats["pending_approval"]}')
                    print(f'   ✅ Approved: {stats["approved"]}')
                    print(f'   📝 Plans: {stats["plans"]}')
                    print(f'   ✔️  Done: {stats["done"]}')
                    print(f'   ⏳ Waiting...')
                    print()

                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            print('\n\n' + '=' * 60)
            print('⏹️  Orchestrator stopped (Ctrl+C)')
            print('=' * 60)
            print(f'📊 Total cycles: {cycle_count}')
            print(f'   Runtime: {cycle_count * self.check_interval} seconds')
            print('=' * 60)
            self.vault_manager.update_dashboard()


if __name__ == '__main__':
    vault_path = Path(__file__).parent.parent
    orchestrator = Orchestrator(vault_path=str(vault_path), check_interval=5)
    orchestrator.run_continuous()
