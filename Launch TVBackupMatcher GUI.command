#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=========================================================="
echo "          🎬 TVBackupMatcher GUI Launcher 📺              "
echo "=========================================================="
echo "  1) Launch Native macOS Desktop Window (Recommended)    "
echo "  2) Launch Web Browser Interface                        "
echo "=========================================================="
read -p "Select interface [1 or 2, default is 1]: " choice

if [ "$choice" = "2" ]; then
    echo "Starting Web Browser GUI..."
    python3 app.py
else
    echo "Starting Native macOS Desktop Window..."
    python3 gui_desktop.py
fi
