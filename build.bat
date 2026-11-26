@echo off
rem MiniToo Mirror - build: tests -> EXE (PyInstaller) -> installer (Inno Setup) -> portable ZIP
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The program is not installed yet - running install.bat first...
    call install.bat
    if not exist ".venv\Scripts\python.exe" exit /b 1
)
for /f "delims=" %%v in ('".venv\Scripts\python.exe" -c "import minitoo; print(minitoo.__version__)"') do set "VERSION=%%v"
echo.
echo === MiniToo Mirror %VERSION% ===

echo.
echo === 1/4 Tests ===
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m unittest discover -s tests -t .
if errorlevel 1 (
    echo Tests failed - nothing was built.
    pause
    exit /b 1
)

echo.
echo === 2/4 EXE (PyInstaller) ===
".venv\Scripts\python.exe" -m pip install --quiet --upgrade "pyinstaller>=6.0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean MiniTooMirror.spec
if errorlevel 1 (
    echo PyInstaller failed.
    pause
    exit /b 1
)
copy /y LICENSE "dist\MiniTooMirror\LICENSE.txt" >nul

echo.
echo === 3/4 Installer (Inno Setup) ===
set "ISCC="
for %%p in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%p set "ISCC=%%~p"
)
if defined ISCC (
    "%ISCC%" /Q /DAppVersion=%VERSION% installer\MiniTooMirror.iss
    if errorlevel 1 (
        echo Inno Setup failed.
        pause
        exit /b 1
    )
) else (
    echo Inno Setup 6 not found - skipping the installer. Get it from https://jrsoftware.org/isdl.php
)

echo.
echo === 4/4 Portable ZIP ===
powershell -NoProfile -Command "Compress-Archive -Path 'dist\MiniTooMirror\*' -DestinationPath 'dist\MiniTooMirror-%VERSION%-portable.zip' -Force"

echo.
echo Done:
if defined ISCC echo   dist\MiniTooMirror-Setup-%VERSION%.exe       - installer
echo   dist\MiniTooMirror-%VERSION%-portable.zip   - portable (unzip and run MiniTooMirror.exe)
echo   dist\MiniTooMirror\                         - the program folder
echo.
pause
