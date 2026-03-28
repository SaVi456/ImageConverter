@echo off
echo === Image Converter Setup ===
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.9+ and try again.
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv venv

echo Installing dependencies...
call venv\Scripts\pip install -r requirements.txt --quiet

echo.
echo Setup complete! Run "run.bat" to launch the app.
pause
