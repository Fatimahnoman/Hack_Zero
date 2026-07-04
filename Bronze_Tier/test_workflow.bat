@echo off
echo ========================================
echo  Quick Test - Bronze Tier
echo ========================================
echo.

cd /d "%~dp0scripts"

echo Running Orchestrator once (manual mode)...
echo.

python orchestrator.py

echo.
echo ========================================
echo Test complete! Check the folders:
echo   - Needs_Action: For action items
echo   - Plans: For Claude-generated plans
echo   - Dashboard.md: For updated stats
echo ========================================
echo.

pause
