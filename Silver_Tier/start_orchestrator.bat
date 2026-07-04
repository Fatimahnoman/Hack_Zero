@echo off
echo ========================================
echo  AI Employee - Silver Tier
echo ========================================
echo.

cd /d "%~dp0scripts"

echoStarting Orchestrator in continuous mode...
echo The Orchestrator will:
echo   1. Monitor Gmail (simulated)
echo   2. Monitor WhatsApp (simulated)
echo   3. Process files automatically
echo   4. Create approval requests
echo   5. Update Dashboard automatically
echo.
echo Press Ctrl+C to stop
echo ========================================
echo.

python orchestrator.py --continuous

pause
