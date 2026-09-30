#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "==================================================="
echo "   Building DRP Trimmer macOS App (.app)"
echo "==================================================="
echo ""

# Find a python with tkinter support
PY_CMD="python3"
if ! python3 -c "import tkinter" &>/dev/null; then
    if python3.13 -c "import tkinter" &>/dev/null; then
        PY_CMD="python3.13"
    elif python3.14 -c "import tkinter" &>/dev/null; then
        PY_CMD="python3.14"
    fi
fi

echo "Using $PY_CMD..."
rm -rf .venv_build
$PY_CMD -m venv .venv_build
.venv_build/bin/pip install --quiet pyinstaller

echo "Compiling .app bundle..."
.venv_build/bin/pyinstaller --windowed --clean --noconfirm --name "DRP Trimmer" "drp_trimmer_gui.py"

rm -rf .venv_build

echo ""
echo "==================================================="
echo "   SUCCESS! App bundle created at:"
echo "   dist/DRP Trimmer.app"
echo "==================================================="
