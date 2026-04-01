#!/usr/bin/env bash
set -e
echo "=== Image Converter Setup ==="
cd "$(dirname "$0")"

if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found. Install Python 3.9+ and try again."
    exit 1
fi

python3 -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" || {
    echo "ERROR: Python 3.9 or newer is required. Found: $(python3 --version)"
    exit 1
}

if [ -d venv ]; then
    echo "Virtual environment already exists. Upgrading..."
    venv/bin/pip install -r requirements.txt --upgrade
else
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "Installing dependencies..."
    venv/bin/pip install -r requirements.txt
fi

echo ""
echo "Setup complete! Run ./run.sh to launch the app."
