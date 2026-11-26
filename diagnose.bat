@echo off
rem MiniToo Mirror - Bluetooth diagnostic. Result: diagnostic_report.txt
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The program is not installed yet - run install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" diagnose.py %*
