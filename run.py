#!/usr/bin/env python3
"""
PhoneCam — Launcher multiplataforma (Windows / macOS / Linux).

Garante que o venv existe, carrega módulo v4l2loopback no Linux se necessário,
e inicia o servidor.

Uso:
  python run.py            # 1280x720 @ 30fps, porta 8765
  python run.py --audio    # vídeo + microfone
  python run.py --width 1920 --height 1080  # Full HD
  python run.py --port 9000 --pin 123456    # porta + PIN fixo

Qualquer argumento extra é repassado para server.py.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
VENV_DIR = PROJECT_DIR / "venv"

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"


def get_venv_python() -> str:
    """Retorna o caminho do python dentro do venv."""
    if IS_WINDOWS:
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python")


def get_venv_pip() -> str:
    if IS_WINDOWS:
        return str(VENV_DIR / "Scripts" / "pip.exe")
    return str(VENV_DIR / "bin" / "pip")


def ensure_venv():
    """Cria o venv se não existir e instala dependências."""
    if VENV_DIR.exists():
        return

    print(">>> venv não encontrado — executando install.py primeiro…")
    py = sys.executable
    try:
        subprocess.run([py, str(PROJECT_DIR / "install.py"), "--skip-system"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERRO] install.py falhou: {e}")
        sys.exit(1)


def ensure_v4l2loopback():
    """No Linux, tenta garantir que o módulo v4l2loopback está carregado.

    Se falhar (sem sudo, sem módulo instalado), apenas avisa e deixa o
    servidor seguir — ele vai mostrar instruções de setup ao tentar criar
    a webcam virtual.
    """
    if not IS_LINUX:
        return

    try:
        r = subprocess.run(["lsmod"], capture_output=True, text=True)
        if "v4l2loopback" in r.stdout:
            return  # já carregado
    except Exception:
        pass

    print(">>> Tentando carregar módulo v4l2loopback…")
    try:
        r = subprocess.run(
            ["sudo", "modprobe", "v4l2loopback",
             "exclusive_caps=1", "video_nr=10", 'card_label=PhoneCam'],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("    [OK] /dev/video10 criado")
        else:
            # Não foi possível carregar — apenas avisa (não aborta)
            print(f"    [!] sudo modprobe falhou: {r.stderr.strip() or r.stdout.strip()}")
            print("    O servidor vai iniciar mesmo assim em modo debug.")
            print("    Se a webcam virtual falhar, rode manualmente:")
            print("      sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label=PhoneCam")
            print()
    except FileNotFoundError:
        print("[!] sudo não encontrado — o servidor vai iniciar em modo debug.")
        print("    Se a webcam virtual falhar, carregue o módulo manualmente:")
        print("      sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label=PhoneCam")
        print()


def main():
    ensure_venv()
    ensure_v4l2loopback()

    # Repassa todos os argumentos para server.py
    py = get_venv_python()
    cmd = [py, str(PROJECT_DIR / "server.py")] + sys.argv[1:]

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[+] Encerrando PhoneCam…")


if __name__ == "__main__":
    main()
