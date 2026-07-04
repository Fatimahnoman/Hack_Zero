"""
Orchestrator - Bronze Tier
Manages the complete workflow: Watcher → Needs_Action → Claude Processing → Done
"""

import time
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from vault_manager import VaultManager


class Orchestrator:
    def __init__(self, vault_path: str, check_interval: int = 5):
        self.vault_path = Path(vault_path)
        self.inbox = self.vault_path / 'Inbox'
        self.needs_action = self.vault_path / 'Needs_Action'
        self.plans = self.vault_path / 'Plans'
        self.done = self.vault_path / 'Done'
        self.logs = self.vault_path / 'Logs'
        self.check_interval = check_interval
        
        # Initialize Vault Manager
        self.vault_manager = VaultManager(vault_path=str(vault_path))
        
        # Setup logging
        log_file = self.logs / f'orchestrator_{datetime.now().strftime("%Y%m%d")}.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('Orchestrator')
        
        # Ensure all folders exist
        for folder in [self.inbox, self.needs_action, self.plans, self.done, self.logs]:
            folder.mkdir(parents=True, exist_ok=True)
        
        self.logger.info('Orchestrator initialized')
        print('=' * 60)
        print('🤖 AI Employee Orchestrator - Bronze Tier')
        print('=' * 60)
        print(f'📂 Vault: {self.vault_path}')
        print(f'⏱️  Check interval: {self.check_interval}s')
        print('=' * 60)
        print()
    
    def check_inbox(self) -> list:
        """Check Inbox for new files"""
        new_files = []
        try:
            for file in self.inbox.iterdir():
                if file.is_file() and not file.name.startswith('.'):
                    new_files.append(file)
        except Exception as e:
            self.logger.error(f'Error checking Inbox: {e}')
        return new_files
    
    def process_file(self, file_path: Path) -> bool:
        """Process a file from Inbox: MOVE it directly to Needs_Action"""
        try:
            self.logger.info(f'Processing file: {file_path.name}')
            print(f'\n📥 Detected new file: {file_path.name}')
            print(f'📂 Moving from Inbox → Needs_Action...')
            
            # Simply MOVE the file from Inbox to Needs_Action
            dest_path = self.needs_action / file_path.name
            file_path.rename(dest_path)
            
            self.logger.info(f'✅ Moved to Needs_Action: {file_path.name}')
            print(f'✅ File moved to Needs_Action')
            print(f'🗑️  Inbox is now empty\n')
            
            return True
            
        except Exception as e:
            self.logger.error(f'Error processing file {file_path.name}: {e}')
            print(f'❌ Error processing file: {e}\n')
            return False
    
    def check_needs_action(self) -> list:
        """Check for pending items in Needs_Action"""
        pending_items = []
        try:
            for file in self.needs_action.iterdir():
                if file.is_file() and not file.name.startswith('.'):
                    pending_items.append(file)
        except Exception as e:
            self.logger.error(f'Error checking Needs_Action: {e}')
        return pending_items
    
    def trigger_claude_processing(self, action_file: Path) -> bool:
        """Process file in Needs_Action: Create plan, then MOVE to Done"""
        try:
            self.logger.info(f'Processing: {action_file.name}')
            print(f'\n🧠 Processing file: {action_file.name}')
            print(f'📂 Creating plan in Plans folder...')
            
            # Read the file content
            file_content = action_file.read_text(encoding='utf-8')
            
            # Extract task name for plan
            task_name = action_file.stem
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Create PERMANENT plan in Plans folder (referencing the original file)
            plan_filename = f'PLAN_{task_name}_{timestamp}.md'
            plan_path = self.plans / plan_filename
            
            plan_content = f'''---
type: plan
created: {datetime.now().isoformat()}
status: active
source_file: {action_file.name}
priority: medium
---

# 📋 Plan: {task_name.replace('_', ' ').title()}

## Source File
Original file: {action_file.name} (now in Done folder)

## File Content
{file_content}

## Action Steps
- [ ] Step 1: Analyze the task requirements
- [ ] Step 2: Execute necessary actions
- [ ] Step 3: Verify completion
- [ ] Step 4: Review and approve

## Notes
<!-- Add your notes and observations here -->

---
*This plan is permanent and will not be deleted.*
*Created by AI Employee Bronze Tier*
'''
            
            plan_path.write_text(plan_content, encoding='utf-8')
            self.logger.info(f'✅ Created permanent plan: {plan_filename}')
            print(f'✅ Permanent plan created in Plans folder')
            
            # MOVE the SAME file from Needs_Action to Done
            done_file = self.done / action_file.name
            action_file.rename(done_file)
            self.logger.info(f'✅ Moved to Done: {action_file.name}')
            print(f'✅ Same file moved to Done')
            print(f'🗑️  Needs_Action is now empty\n')
            
            return True
            
        except Exception as e:
            self.logger.error(f'Error processing action {action_file.name}: {e}')
            print(f'❌ Error processing action: {e}\n')
            return False
    
    def update_dashboard(self):
        """Update Dashboard with current stats"""
        stats = self.vault_manager.get_stats()
        self.vault_manager.update_dashboard(
            pending_count=stats['pending'],
            done_count=stats['done']
        )
    
    def run_once(self):
        """Run one complete cycle"""
        items_processed = 0
        
        # Step 1: Check Inbox for new files
        new_files = self.check_inbox()
        if new_files:
            print(f'\n📂 Found {len(new_files)} new file(s) in Inbox')
            for file in new_files:
                if self.process_file(file):
                    items_processed += 1
        
        # Step 2: Check Needs_Action for pending items and process them
        pending_items = self.check_needs_action()
        if pending_items:
            print(f'\n📋 Found {len(pending_items)} pending item(s) in Needs_Action')
            for item in pending_items:
                if self.trigger_claude_processing(item):
                    items_processed += 1
        
        # Step 3: Update Dashboard
        self.update_dashboard()
        
        # Return total items processed
        return items_processed
    
    def run_continuous(self):
        """Run continuously in a loop"""
        print('🔄 Starting continuous monitoring mode...')
        print('Press Ctrl+C to stop\n')
        
        cycle_count = 0
        
        try:
            while True:
                cycle_count += 1
                items_processed = self.run_once()
                
                if items_processed > 0:
                    self.logger.info(f'Cycle {cycle_count}: Processed {items_processed} item(s)')
                else:
                    # Only log every 10 cycles when idle to reduce noise
                    if cycle_count % 10 == 0:
                        self.logger.info(f'Cycle {cycle_count}: No new items (idle)')
                
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            self.logger.info('Orchestrator stopped by user')
            print('\n⏹️  Orchestrator stopped')
            print(f'📊 Total cycles run: {cycle_count}')
            self.update_dashboard()
    
    def run_manual(self):
        """Run once for manual processing"""
        print('\n🔵 Running manual processing cycle...\n')
        items_processed = self.run_once()
        
        if items_processed == 0:
            print('✅ No new items to process')
        else:
            print(f'\n✅ Processing complete: {items_processed} item(s) handled')
        
        self.update_dashboard()


if __name__ == '__main__':
    # Get vault path (parent of scripts folder)
    vault_path = Path(__file__).parent.parent
    
    # Create orchestrator
    orchestrator = Orchestrator(vault_path=str(vault_path))
    
    # Choose mode based on command line argument
    import sys
    if '--continuous' in sys.argv or '-c' in sys.argv:
        orchestrator.run_continuous()
    else:
        orchestrator.run_manual()
