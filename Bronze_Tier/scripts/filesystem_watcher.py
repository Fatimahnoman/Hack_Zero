"""
File System Watcher - Bronze Tier
Monitors the Inbox folder and creates action files in Needs_Action when files are added.
"""

import time
import logging
from pathlib import Path
from datetime import datetime


class FileSystemWatcher:
    def __init__(self, vault_path: str, check_interval: int = 5):
        self.vault_path = Path(vault_path)
        self.inbox = self.vault_path / 'Inbox'
        self.needs_action = self.vault_path / 'Needs_Action'
        self.check_interval = check_interval
        self.processed_files = set()
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.vault_path / 'Logs' / 'filesystem_watcher.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('FileSystemWatcher')
        
        # Ensure folders exist
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.needs_action.mkdir(parents=True, exist_ok=True)
        
    def check_for_new_files(self) -> list:
        """Check for new files in Inbox folder"""
        try:
            new_files = []
            for file in self.inbox.iterdir():
                if file.is_file() and file.name not in self.processed_files:
                    new_files.append(file)
            return new_files
        except Exception as e:
            self.logger.error(f'Error checking Inbox folder: {e}')
            return []
    
    def create_action_file(self, source_file: Path) -> Path:
        """Create an action .md file in Needs_Action folder"""
        try:
            # Read file content
            content = source_file.read_text(encoding='utf-8', errors='ignore')
            
            # Create markdown action file
            action_content = f'''---
type: file_drop
original_name: {source_file.name}
detected: {datetime.now().isoformat()}
priority: medium
status: pending
---

## File Detected: {source_file.name}

**Source:** Inbox/{source_file.name}

**Content Preview:**
{content[:500]}{'...' if len(content) > 500 else ''}

## Suggested Actions
- [ ] Review file content
- [ ] Process or archive
- [ ] Move to Done when complete
'''
            
            # Create action file
            action_filename = f'FILE_{source_file.stem}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.md'
            action_path = self.needs_action / action_filename
            action_path.write_text(action_content, encoding='utf-8')
            
            # Mark as processed
            self.processed_files.add(source_file.name)
            
            self.logger.info(f'Created action file: {action_filename}')
            return action_path
            
        except Exception as e:
            self.logger.error(f'Error creating action file for {source_file.name}: {e}')
            return None
    
    def run(self):
        """Main loop - continuously monitor Inbox"""
        self.logger.info('Starting File System Watcher...')
        self.logger.info(f'Monitoring: {self.inbox}')
        self.logger.info(f'Output: {self.needs_action}')
        print(f'\n✅ File System Watcher is running!')
        print(f'📂 Drop files in: {self.inbox}')
        print(f'📝 Action files created in: {self.needs_action}')
        print('Press Ctrl+C to stop\n')
        
        try:
            while True:
                new_files = self.check_for_new_files()
                for file in new_files:
                    self.create_action_file(file)
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.logger.info('Watcher stopped by user')
            print('\n⏹️  File System Watcher stopped.')


if __name__ == '__main__':
    # Get the vault path (parent of Bronze_Tier)
    vault_path = Path(__file__).parent.parent
    
    # Start watcher
    watcher = FileSystemWatcher(vault_path=str(vault_path))
    watcher.run()
