"""
Approval Handler - Silver Tier (STRICT HITL)
=============================================
Human-in-the-Loop approval workflow with strict verification.

Flow:
1. Orchestrator creates approval request in Pending_Approval/
2. WAITS for human approval
3. Human reviews draft reply → Moves file to Approved/
4. Orchestrator sends via MCP
5. ✅ Success → Move to Done/
6. ❌ Failed → STAYS in Approved/ (retry manually)
"""

from pathlib import Path
from datetime import datetime
import shutil
import logging


class ApprovalHandler:
    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self.pending_approval = self.vault_path / 'Pending_Approval'
        self.approved = self.vault_path / 'Approved'
        self.done = self.vault_path / 'Done'
        self.logs = self.vault_path / 'Logs'

        # Setup logging
        log_file = self.logs / f'approval_{datetime.now().strftime("%Y%m%d")}.log'

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        stream_handler = logging.StreamHandler()

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                file_handler,
                stream_handler
            ]
        )
        self.logger = logging.getLogger('ApprovalHandler')

        # Ensure folders exist
        for folder in [self.pending_approval, self.approved, self.done, self.logs]:
            folder.mkdir(parents=True, exist_ok=True)

    def create_approval_request(self, source_file: str, message_content: str, reply_draft: str, plan_file: str) -> Path:
        """
        Create an approval request file in Pending_Approval.
        Contains original message + AI-generated draft reply.
        Human must review and move to Approved/ folder.
        """
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'APPROVAL_{Path(source_file).stem}_{timestamp}.md'
            filepath = self.pending_approval / filename

            content = f'''---
type: approval_request
action: send_whatsapp_reply
created: {datetime.now().isoformat()}
status: pending
source_file: {source_file}
plan_file: {plan_file}
requires: human_approval
strict_verification: true
---

# 🔐 APPROVAL REQUIRED: Send WhatsApp Reply

## ⚠️ ACTION REQUIRED
This message requires your approval before the AI can send a reply.

---

## 📥 Original Message Received

{message_content}

---

## 💬 AI-Generated Draft Reply

{reply_draft}

---

## 📋 Reply Details
- **Action:** Send WhatsApp reply via MCP
- **Source:** {source_file}
- **Plan:** {plan_file}
- **Draft Length:** {len(reply_draft)} characters

---

## ✅ HOW TO APPROVE

**Option 1: Approve (Send Reply)**
1. Review the draft reply above
2. Edit if needed (optional)
3. Move this file to: `Approved/` folder
4. Orchestrator will send it automatically

**Option 2: Reject (Don't Send)**
1. Move this file to: `Rejected/` folder
2. Reply will NOT be sent

---

## ⚠️ STRICT VERIFICATION

After approval:
- ✅ MCP sends message successfully → File moves to `Done/`
- ❌ MCP fails to send → **File STAYS in `Approved/`** (requires manual retry)

**IMPORTANT:** If file remains in `Approved/`, it means MCP failed.
You must manually retry or investigate the issue.

---

## 🔍 Audit Trail

| Step | Status | Timestamp |
|------|--------|-----------|
| Message Received | ✅ | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
| Draft Generated | ✅ | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} |
| Approval Request | ⏳ | Waiting for human |
| Human Approval | ⏳ | Pending |
| MCP Send | ⏳ | Not attempted |
| Final Status | ⏳ | Unknown |

---

*Created by AI Employee Silver Tier - Human-in-the-Loop (STRICT)*
*This file requires your explicit action before proceeding.*
'''

            filepath.write_text(content, encoding='utf-8')
            self.logger.info(f'Created approval request: {filename}')
            print(f'   🔐 Approval request created: {filename}')
            print(f'   📂 Location: Pending_Approval/')
            print(f'   ⏳ WAITING for human approval...')
            return filepath

        except Exception as e:
            self.logger.error(f'Error creating approval request: {e}')
            print(f'   ❌ Error creating approval request: {e}')
            return None

    def check_approved(self) -> list:
        """Check for approved files in Approved folder"""
        approved_files = []
        try:
            for file in self.approved.iterdir():
                if file.is_file() and file.suffix == '.md':
                    approved_files.append(file)
        except Exception as e:
            self.logger.error(f'Error checking Approved folder: {e}')
        return approved_files

    def process_approved_file(self, file_path: Path, mcp_send_function) -> dict:
        """
        Process an approved file and send via MCP.
        STRICT VERIFICATION:
        - ✅ Success → Move to Done/
        - ❌ Failed → STAY in Approved/ (retry manually)

        Args:
            file_path: Path to approved file
            mcp_send_function: Function to call MCP (contact, message) -> bool
        """
        try:
            print(f'\n🔐 Processing approved file: {file_path.name}')

            # Read approval file content
            content = file_path.read_text(encoding='utf-8')

            # Extract details
            source_file = self._extract_field(content, 'source_file')
            plan_file = self._extract_field(content, 'plan_file')

            # Extract draft reply from content
            draft_reply = self._extract_draft_reply(content)

            # Extract contact name
            contact_name = self._extract_contact_name(source_file)

            print(f'   📤 Sending reply via MCP...')
            print(f'   Contact: {contact_name}')
            print(f'   Message preview: {draft_reply[:100]}...')

            # Call MCP to send message
            mcp_success = mcp_send_function(contact_name, draft_reply)

            if mcp_success:
                # ✅ SUCCESS - Move to Done
                print(f'   ✅ MCP send successful!')

                # Update file with success status
                updated_content = content.replace(
                    'status: pending',
                    'status: approved_and_sent'
                ).replace(
                    f'Human Approval | ⏳ | Pending',
                    f'Human Approval | ✅ | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                ).replace(
                    f'MCP Send | ⏳ | Not attempted',
                    f'MCP Send | ✅ | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                ).replace(
                    f'Final Status | ⏳ | Unknown',
                    f'Final Status | ✅ | Sent Successfully'
                )

                file_path.write_text(updated_content, encoding='utf-8')

                # Move to Done
                done_file = self.done / file_path.name
                file_path.rename(done_file)

                print(f'   ✅ File moved to Done/')

                self.logger.info(f'Approved file sent and moved to Done: {file_path.name}')

                return {
                    'status': 'success',
                    'action': 'sent_and_moved_to_done',
                    'contact': contact_name,
                    'source_file': source_file
                }
            else:
                # ❌ FAILED - STAY in Approved/ (STRICT VERIFICATION)
                print(f'   ❌ MCP send FAILED!')
                print(f'   ⚠️  File STAYS in Approved/ folder')
                print(f'   🔍 Manual intervention required!')

                # Update file with failure status
                updated_content = content.replace(
                    'status: pending',
                    'status: approved_but_send_failed'
                ).replace(
                    f'Human Approval | ⏳ | Pending',
                    f'Human Approval | ✅ | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                ).replace(
                    f'MCP Send | ⏳ | Not attempted',
                    f'MCP Send | ❌ FAILED | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
                ).replace(
                    f'Final Status | ⏳ | Unknown',
                    f'Final Status | ❌ | MCP Failed - Manual Retry Required'
                ).replace(
                    '## ⚠️ STRICT VERIFICATION',
                    f'''## ⚠️ STRICT VERIFICATION - SEND FAILED

**MCP failed to send message at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}**

**This file remains in Approved/ folder until issue is resolved.**

**Manual Steps Required:**
1. Check WhatsApp MCP server status
2. Verify WhatsApp session is active
3. Retry sending manually or restart orchestrator
4. Once sent, move this file to Done/ manually
'''
                )

                file_path.write_text(updated_content, encoding='utf-8')

                self.logger.error(f'Approved file MCP send failed: {file_path.name}')

                return {
                    'status': 'failed_mcp',
                    'action': 'stayed_in_approved',
                    'contact': contact_name,
                    'source_file': source_file,
                    'note': 'File remains in Approved/ for manual retry'
                }

        except Exception as e:
            self.logger.error(f'Error processing approved file {file_path.name}: {e}')
            print(f'   ❌ Error: {e}')
            return {
                'status': 'error',
                'action': 'error',
                'error': str(e)
            }

    def _extract_field(self, content: str, field: str) -> str:
        """Extract a field from YAML frontmatter"""
        for line in content.split('\n'):
            if line.startswith(field + ':'):
                return line.split(':', 1)[1].strip()
        return ''

    def _extract_draft_reply(self, content: str) -> str:
        """Extract draft reply from approval file content"""
        try:
            # Look for draft reply section
            start_marker = '## 💬 AI-Generated Draft Reply'
            end_marker = '---'

            start_idx = content.find(start_marker)
            if start_idx == -1:
                return ''

            start_idx += len(start_marker)

            # Find next section
            remaining = content[start_idx:]
            end_idx = remaining.find(end_marker)

            if end_idx == -1:
                return remaining.strip()

            return remaining[:end_idx].strip()

        except:
            return 'Reply draft not found'

    def _extract_contact_name(self, source_file: str) -> str:
        """Extract contact name from source filename"""
        try:
            stem = Path(source_file).stem
            parts = stem.split('_')
            if len(parts) >= 3:
                contact_parts = parts[1:-2]
                return ' '.join(contact_parts).replace('_', ' ')
            return 'Unknown Contact'
        except:
            return 'Unknown Contact'

    def get_pending_count(self) -> int:
        """Get count of pending approvals"""
        try:
            return len(list(self.pending_approval.glob('*.md')))
        except:
            return 0

    def get_approved_count(self) -> int:
        """Get count of approved items (including failed)"""
        try:
            return len(list(self.approved.glob('*.md')))
        except:
            return 0
