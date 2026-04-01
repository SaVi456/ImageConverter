#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -f venv/bin/activate ]; then
    echo "ERROR: Virtual environment not found. Run ./setup.sh first."
    exit 1
fi
source venv/bin/activate
python image_converter.py
