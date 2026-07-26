#!/usr/bin/env python3
"""
PhoneCam — Instalador multiplataforma (Windows / macOS / Linux).

Detecta o OS, instala dependências de sistema via o gerenciador de pacotes
apropriado, cria venv e instala dependências Python.

Uso:
  python install.py            # instala tudo
  python install.py --skip-system  # pula instalação de pacotes de sistema (requer sudo)
  python install.py --audio-only   # só instala deps de áudio

Compatível com: Windows 10+, macOS 11+, Ubuntu/Debian, Fedora, Arch Linux.
"""

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
VENV_DIR = PROJECT_DIR / "venv"
REQUIREMENTS = PROJECT_DIR / "requirements.txt"

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS   = platform.system() == "Darwin"
IS_LINUX   = platform.system() == "Linux"
OS_NAME    = "Windows" if IS_WINDOWS else "macOS" if IS_MACOS else "Linux"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def run(cmd, check=True, capture=False, shell=False):
    """Roda um comando e retorna o resultado."""
    print(f">>> {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        if check and r.returncode != 0:
            print(f"[ERRO] comando falhou (exit {r.returncode})")
            if r.stderr: print(r.stderr)
            sys.exit(1)
        return r
    else:
        r = subprocess.run(cmd, shell=shell)
        if check and r.returncode != 0:
            print(f"[ERRO] comando falhou (exit {r.returncode})")
            sys.exit(1)
        return r


def have_sudo() -> bool:
    """Verifica se consegue usar sudo (Linux/macOS)."""
    if IS_WINDOWS:
        return False
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------- #
# Linux: instalar pacotes via apt/dnf/pacman
# --------------------------------------------------------------------------- #
def install_linux_system():
    print(f"\n=== Detectando distribuição Linux ===")

    # v4l2loopback + v4l-utils + portaudio + python3-venv
    if _has_cmd("apt-get"):
        print(">>> Detectado: Debian/Ubuntu (apt-get)")
        if not have_sudo():
            print("[!] Sem sudo passwordless — você precisará digitar a senha.")
        kernel_headers = f"linux-headers-$(uname -r)"
        run(["sudo", "apt-get", "update", "-y"], check=False)
        run([
            "sudo", "apt-get", "install", "-y",
            "v4l2loopback-dkms", "v4l-utils",
            "python3-pip", "python3-venv", "dkms",
            kernel_headers,
            "portaudio19-dev",
        ], check=False)
    elif _has_cmd("dnf"):
        print(">>> Detectado: Fedora/RHEL (dnf)")
        run(["sudo", "dnf", "install", "-y",
             "v4l2loopback", "v4l-utils",
             "python3-pip", "python3-virtualenv", "dkms",
             "kernel-devel", "kernel-headers",
             "portaudio-devel"], check=False)
    elif _has_cmd("pacman"):
        print(">>> Detectado: Arch Linux (pacman)")
        # Detecta o kernel para instalar o headers correto
        kver = platform.release()
        if "-lts" in kver:
            headers = "linux-lts-headers"
        elif "-zen" in kver:
            headers = "linux-zen-headers"
        elif "-hardened" in kver:
            headers = "linux-hardened-headers"
        else:
            headers = "linux-headers"
        run(["sudo", "pacman", "-S", "--noconfirm", "--needed",
             "v4l2loopback-dkms", "v4l-utils",
             "python-pip", "python-virtualenv", "dkms",
             "portaudio",
             headers], check=False)
    else:
        print("[!] Gerenciador de pacotes não detectado.")
        print("    Instale manualmente: v4l2loopback-dkms, v4l-utils, portaudio19-dev, python3-venv")

    # Carrega módulo v4l2loopback
    print("\n>>> Carregando módulo v4l2loopback…")
    try:
        # Verifica se já está carregado
        r = subprocess.run(["lsmod"], capture_output=True, text=True)
        if "v4l2loopback" not in r.stdout:
            run(["sudo", "modprobe", "v4l2loopback",
                 "exclusive_caps=1", "video_nr=10", 'card_label=PhoneCam'])
            print("    [OK] /dev/video10 criado")
        else:
            print("    (módulo já carregado)")
    except Exception as e:
        print(f"    [!] {e}")

    # Persistência no boot
    try:
        modprobe_conf = "/etc/modprobe.d/phonecam.conf"
        modules_conf = "/etc/modules-load.d/phonecam.conf"
        run(["sudo", "tee", modprobe_conf],
            check=False, capture=True,
            shell=False)  # placeholder
        # Escreve via shell para evitar complicações com sudo tee
        subprocess.run(
            f'echo "options v4l2loopback exclusive_caps=1 video_nr=10 card_label=\\"PhoneCam\\"" | sudo tee {modprobe_conf} > /dev/null',
            shell=True, check=False,
        )
        subprocess.run(
            f'echo "v4l2loopback" | sudo tee {modules_conf} > /dev/null',
            shell=True, check=False,
        )
        print(f"    [OK] Persistência configurada em {modprobe_conf}")
    except Exception as e:
        print(f"    [!] {e}")


# --------------------------------------------------------------------------- #
# macOS: instalar via Homebrew
# --------------------------------------------------------------------------- #
def install_macos_system():
    print(f"\n=== Setup macOS ===")

    # Verifica Homebrew
    if not _has_cmd("brew"):
        print("[!] Homebrew não encontrado.")
        print("    Instale com:")
        print('    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        print("    Depois rode: python install.py")
        sys.exit(1)

    print(">>> Instalando portaudio via Homebrew…")
    run(["brew", "install", "portaudio"], check=False)

    # Verifica se OBS Studio está instalado (necessário para webcam virtual)
    obs_app = Path("/Applications/OBS.app")
    if not obs_app.exists():
        print("\n[!] OBS Studio não encontrado em /Applications.")
        print("    Para webcam virtual no macOS você precisa do OBS Studio:")
        print("    https://obsproject.com/download")
        print("    Após instalar, abra o OBS uma vez e ative 'Virtual Camera'.")

    # BlackHole para áudio (opcional, só avisa)
    print("\n>>> Para microfone virtual no macOS, instale BlackHole:")
    print("    https://existential.audio/blackhole/")
    print("    (Necessário apenas se for usar --audio)")


# --------------------------------------------------------------------------- #
# Windows: instruções + verificações
# --------------------------------------------------------------------------- #
def install_windows_system():
    print(f"\n=== Setup Windows ===")
    print("[info] No Windows, as dependências são gerenciadas pelo pip com wheels pré-compiladas.")
    print("       pyaudio e pyvirtualcam têm wheels para Windows — não precisa de compilação.")

    # Verifica se OBS Studio está instalado
    obs_paths = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "obs-studio",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "obs-studio",
    ]
    obs_installed = any(p.exists() for p in obs_paths)
    if not obs_installed:
        print("\n[!] OBS Studio não encontrado.")
        print("    Para webcam virtual no Windows você precisa do OBS Studio:")
        print("    https://obsproject.com/download")
        print("    Após instalar, abra o OBS uma vez e ative 'Start Virtual Camera'.")

    print("\n>>> Para microfone virtual no Windows, instale VB-Cable:")
    print("    https://vb-audio.com/Cable/")
    print("    (Necessário apenas se for usar --audio)")


# --------------------------------------------------------------------------- #
# venv Python (comum a todos OSes)
# --------------------------------------------------------------------------- #
def setup_venv():
    print(f"\n=== Criando venv Python em {VENV_DIR} ===")

    # Detecta python3 vs python
    py_cmd = sys.executable  # usa o python que está rodando este script
    if not py_cmd:
        py_cmd = "python3" if not IS_WINDOWS else "python"

    if VENV_DIR.exists():
        print(f"    (venv já existe em {VENV_DIR} — reusando)")
    else:
        run([py_cmd, "-m", "venv", str(VENV_DIR)])

    # Ativa e instala
    if IS_WINDOWS:
        pip = str(VENV_DIR / "Scripts" / "pip.exe")
        py  = str(VENV_DIR / "Scripts" / "python.exe")
    else:
        pip = str(VENV_DIR / "bin" / "pip")
        py  = str(VENV_DIR / "bin" / "python")

    print("\n>>> Atualizando pip…")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "wheel"], check=False)

    print("\n>>> Instalando dependências Python…")
    # Tenta instalar tudo. Se pyaudio falhar (sem portaudio), instala o resto
    r = subprocess.run([pip, "install", "-r", str(REQUIREMENTS)])
    if r.returncode != 0:
        print("\n[!] Falha ao instalar uma ou mais dependências.")
        print("    Tentando instalar pacotes essenciais individualmente…")
        essential = ["fastapi", "uvicorn[standard]", "pillow", "numpy",
                     "cryptography", "qrcode[pil]"]
        for pkg in essential:
            subprocess.run([pip, "install", pkg], check=False)

        # Tenta pyaudio e pyvirtualcam separadamente para mensagem clara
        print("\n>>> Tentando instalar pyaudio (opcional, para microfone)…")
        r2 = subprocess.run([pip, "install", "pyaudio"])
        if r2.returncode != 0:
            print("[!] pyaudio não instalado — microfone do celular não vai funcionar.")
            if IS_LINUX:
                print("    Instale portaudio19-dev primeiro:")
                print("      Arch:    sudo pacman -S portaudio")
                print("      Ubuntu:  sudo apt install portaudio19-dev")
                print("      Fedora:  sudo dnf install portaudio-devel")
            elif IS_MACOS:
                print("    Instale portaudio primeiro: brew install portaudio")
            elif IS_WINDOWS:
                print("    No Windows tente: pip install pipwin && pipwin install pyaudio")

        print("\n>>> Tentando instalar pyvirtualcam (necessário para webcam virtual)…")
        r3 = subprocess.run([pip, "install", "pyvirtualcam"])
        if r3.returncode != 0:
            print("[!] pyvirtualcam não instalado — webcam virtual não vai funcionar.")

    print(f"\n[OK] venv pronto em {VENV_DIR}")


# --------------------------------------------------------------------------- #
# Util
# --------------------------------------------------------------------------- #
def _has_cmd(name: str) -> bool:
    """Verifica se um comando existe no PATH."""
    from shutil import which
    return which(name) is not None


def main():
    parser = argparse.ArgumentParser(description="PhoneCam — instalador multiplataforma")
    parser.add_argument("--skip-system", action="store_true",
                        help="Pula instalação de pacotes de sistema (requer sudo no Linux)")
    parser.add_argument("--audio-only", action="store_true",
                        help="Só instala deps de áudio (portaudio/blackhole/vb-cable)")
    args = parser.parse_args()

    print("=" * 64)
    print("  PhoneCam — Instalador")
    print(f"  OS detectado: {OS_NAME}")
    print("=" * 64)

    if not args.skip_system:
        if IS_LINUX:
            install_linux_system()
        elif IS_MACOS:
            install_macos_system()
        elif IS_WINDOWS:
            install_windows_system()
        else:
            print(f"[!] OS não suportado: {OS_NAME}")
            sys.exit(1)

    setup_venv()

    print("\n" + "=" * 64)
    print(" Instalação concluída!")
    print("=" * 64)
    print()
    print("Para iniciar o PhoneCam:")
    if IS_WINDOWS:
        print("  venv\\Scripts\\python.exe server.py")
        print("  ou: run.bat")
    else:
        print("  ./run.py")
        print("  ou: ./run.sh")
    print()
    print("Para usar microfone do celular, adicione --audio:")
    print("  ./run.py --audio   (Linux/macOS)")
    print("  python server.py --audio   (Windows)")
    print()


if __name__ == "__main__":
    main()
