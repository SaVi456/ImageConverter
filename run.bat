@echo off
cd /d "%~dp0"
if not exist venv\Scripts\activate (
    echo ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)
call venv\Scripts\activate
python image_converter.py
