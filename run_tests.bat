@echo off
rem MiniToo Mirror - automatic tests (no device needed)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The program is not installed yet - run install.bat first.
    pause
    exit /b 1
)
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m unittest discover -s tests -t . -v
pause
