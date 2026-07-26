#!/usr/bin/env bash

set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[ERROR] Python 3 not found. Install with:"
    echo "  Arch:    sudo pacman -S python"
    echo "  Ubuntu:  sudo apt install python3"
    echo "  Fedora:  sudo dnf install python3"
    echo "  macOS:   brew install python"
    exit 1
fi

exec "$PY" install.py "$@"