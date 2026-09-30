#!/usr/bin/env bash
cd "$(dirname "$0")"

# Create venv if not present
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
fi

echo "Starting DRP Trimmer..."
exec ./.venv/bin/python drp_trimmer_gui.py "$@"
