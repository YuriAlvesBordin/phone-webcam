#!/usr/bin/env bash
#
# Launcher para Linux/macOS — chama run.py (multiplataforma).
#
# Uso:
#   ./run.sh                              # 1280x720 @ 30fps, porta 8765
#   ./run.sh --audio                      # vídeo + microfone
#   ./run.sh --width 1920 --height 1080  # Full HD
#   ./run.sh --port 9000 --pin 123456    # porta + PIN fixo
#
set -e
cd "$(dirname "$0")"

# Detecta python3
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[ERRO] Python 3 não encontrado. Instale com:"
    echo "  Arch:    sudo pacman -S python"
    echo "  Ubuntu:  sudo apt install python3"
    echo "  Fedora:  sudo dnf install python3"
    echo "  macOS:   brew install python"
    exit 1
fi

exec "$PY" run.py "$@"
