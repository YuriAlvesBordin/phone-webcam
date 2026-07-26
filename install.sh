#!/usr/bin/env bash
#
# Instalador para Linux/macOS — chama install.py (multiplataforma).
#
# Uso:
#   ./install.sh                # instala tudo (incluindo pacotes de sistema)
#   ./install.sh --skip-system  # pula pacotes de sistema (só venv + pip)
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

exec "$PY" install.py "$@"
