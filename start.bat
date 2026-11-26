@echo off
rem MiniToo Mirror - start the program (start.bat --simulator = without the device)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo The program is not installed yet - run install.bat first.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app.py %*
