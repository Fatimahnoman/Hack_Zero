@echo off
echo ========================================
echo  AI Employee - Bronze Tier
echo ========================================
echo.

cd /d "%~dp0scripts"

echo Starting Orchestrator in continuous mode...
echo The Orchestrator will:
echo   1. Monitor Inbox folder for new files
echo   2. Process files and create action items
echo   3. Trigger Claude Code for analysis
echo   4. Update Dashboard automatically
echo.
echo Press Ctrl+C to stop
echo ========================================
echo.

python orchestrator.py --continuous

pause
