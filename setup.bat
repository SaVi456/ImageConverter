@echo off
echo === Image Converter Setup ===
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.9+ from https://python.org and try again.
    pause
    exit /b 1
)

python -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.9 or newer is required.
    python --version
    pause
    exit /b 1
)

if exist venv (
    echo Virtual environment already exists. Upgrading...
    call venv\Scripts\pip install -r requirements.txt --upgrade
) else (
    echo Creating virtual environment...
    python -m venv venv
    echo Installing dependencies...
    call venv\Scripts\pip install -r requirements.txt
)

if errorlevel 1 (
    echo ERROR: Dependency installation failed. See messages above.
    pause
    exit /b 1
)

echo.
echo Setup complete! Run run.bat to launch the app.
pause
