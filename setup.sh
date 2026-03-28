#!/usr/bin/env bash
set -e
echo "=== Image Converter Setup ==="
cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found. Install Python 3.9+ and try again."
    exit 1
fi

echo "Creating virtual environment..."
python3 -m venv venv

echo "Installing dependencies..."
venv/bin/pip install -r requirements.txt --quiet

echo ""
echo "Setup complete! Run './run.sh' to launch the app."
