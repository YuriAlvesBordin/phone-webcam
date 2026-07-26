#!/usr/bin/env python3

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


def run(cmd, check=True, capture=False, shell=False):
    print(f">>> {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True, shell=shell)
        if check and r.returncode != 0:
            print(f"[ERROR] command failed (exit {r.returncode})")
            if r.stderr:
                print(r.stderr)
            sys.exit(1)
        return r
    else:
        r = subprocess.run(cmd, shell=shell)
        if check and r.returncode != 0:
            print(f"[ERROR] command failed (exit {r.returncode})")
            sys.exit(1)
        return r


def have_sudo() -> bool:
    if IS_WINDOWS:
        return False
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def install_linux_system():
    print("\n=== Detecting Linux distribution ===")

    if _has_cmd("apt-get"):
        print(">>> Detected: Debian/Ubuntu (apt-get)")
        if not have_sudo():
            print("[!] No passwordless sudo — you will need to enter password.")
        kernel_headers = "linux-headers-$(uname -r)"
        run(["sudo", "apt-get", "update", "-y"], check=False)
        run([
            "sudo", "apt-get", "install", "-y",
            "v4l2loopback-dkms", "v4l-utils",
            "python3-pip", "python3-venv", "dkms",
            kernel_headers,
            "portaudio19-dev",
        ], check=False)
    elif _has_cmd("dnf"):
        print(">>> Detected: Fedora/RHEL (dnf)")
        run(["sudo", "dnf", "install", "-y",
             "v4l2loopback", "v4l-utils",
             "python3-pip", "python3-virtualenv", "dkms",
             "kernel-devel", "kernel-headers",
             "portaudio-devel"], check=False)
    elif _has_cmd("pacman"):
        print(">>> Detected: Arch Linux (pacman)")
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
        print("[!] Package manager not detected.")
        print("    Install manually: v4l2loopback-dkms, v4l-utils, portaudio19-dev, python3-venv")

    print("\n>>> Loading v4l2loopback module...")
    try:
        r = subprocess.run(["lsmod"], capture_output=True, text=True)
        if "v4l2loopback" not in r.stdout:
            run(["sudo", "modprobe", "v4l2loopback",
                 "exclusive_caps=1", "video_nr=10", 'card_label=PhoneCam'])
            print("    [OK] /dev/video10 created")
        else:
            print("    (module already loaded)")
    except Exception as e:
        print(f"    [!] {e}")

    # Boot persistence
    try:
        modprobe_conf = "/etc/modprobe.d/phonecam.conf"
        modules_conf = "/etc/modules-load.d/phonecam.conf"
        run(["sudo", "tee", modprobe_conf],
            check=False, capture=True,
            shell=False)
        subprocess.run(
            f'echo "options v4l2loopback exclusive_caps=1 video_nr=10 card_label=\\\"PhoneCam\\\"" | sudo tee {modprobe_conf} > /dev/null',
            shell=True, check=False,
        )
        subprocess.run(
            f'echo "v4l2loopback" | sudo tee {modules_conf} > /dev/null',
            shell=True, check=False,
        )
        print(f"    [OK] Persistence configured in {modprobe_conf}")
    except Exception as e:
        print(f"    [!] {e}")


def install_macos_system():
    print("\n=== macOS Setup ===")

    if not _has_cmd("brew"):
        print("[!] Homebrew not found.")
        print("    Install with:")
        print('    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
        print("    Then run: python install.py")
        sys.exit(1)

    print(">>> Installing portaudio via Homebrew...")
    run(["brew", "install", "portaudio"], check=False)

    obs_app = Path("/Applications/OBS.app")
    if not obs_app.exists():
        print("\n[!] OBS Studio not found in /Applications.")
        print("    For virtual webcam on macOS you need OBS Studio:")
        print("    https://obsproject.com/download")
        print("    After installing, open OBS once and enable 'Virtual Camera'.")

    print("\n>>> For virtual microphone on macOS, install BlackHole:")
    print("    https://existential.audio/blackhole/")
    print("    (Only needed if using --audio)")


def install_windows_system():
    print("\n=== Windows Setup ===")
    print("[info] On Windows, dependencies are managed by pip with pre-compiled wheels.")
    print("       pyaudio and pyvirtualcam have wheels for Windows — no compilation needed.")

    obs_paths = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "obs-studio",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "obs-studio",
    ]
    obs_installed = any(p.exists() for p in obs_paths)
    if not obs_installed:
        print("\n[!] OBS Studio not found.")
        print("    For virtual webcam on Windows you need OBS Studio:")
        print("    https://obsproject.com/download")
        print("    After installing, open OBS once and enable 'Start Virtual Camera'.")

    print("\n>>> For virtual microphone on Windows, install VB-Cable:")
    print("    https://vb-audio.com/Cable/")
    print("    (Only needed if using --audio)")


def setup_venv():
    print(f"\n=== Creating Python venv at {VENV_DIR} ===")

    py_cmd = sys.executable
    if not py_cmd:
        py_cmd = "python3" if not IS_WINDOWS else "python"

    if VENV_DIR.exists():
        print(f"    (venv already exists at {VENV_DIR} — reusing)")
    else:
        run([py_cmd, "-m", "venv", str(VENV_DIR)])

    if IS_WINDOWS:
        pip = str(VENV_DIR / "Scripts" / "pip.exe")
        py  = str(VENV_DIR / "Scripts" / "python.exe")
    else:
        pip = str(VENV_DIR / "bin" / "pip")
        py  = str(VENV_DIR / "bin" / "python")

    print("\n>>> Upgrading pip...")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "wheel"], check=False)

    print("\n>>> Installing Python dependencies...")
    r = subprocess.run([pip, "install", "-r", str(REQUIREMENTS)])
    if r.returncode != 0:
        print("\n[!] Failed to install one or more dependencies.")
        print("    Trying to install essential packages individually...")
        essential = ["fastapi", "uvicorn[standard]", "pillow", "numpy",
                     "cryptography", "qrcode[pil]"]
        for pkg in essential:
            subprocess.run([pip, "install", pkg], check=False)

        print("\n>>> Trying to install pyaudio (optional, for microphone)...")
        r2 = subprocess.run([pip, "install", "pyaudio"])
        if r2.returncode != 0:
            print("[!] pyaudio not installed — phone microphone won't work.")
            if IS_LINUX:
                print("    Install portaudio19-dev first:")
                print("      Arch:    sudo pacman -S portaudio")
                print("      Ubuntu:  sudo apt install portaudio19-dev")
                print("      Fedora:  sudo dnf install portaudio-devel")
            elif IS_MACOS:
                print("    Install portaudio first: brew install portaudio")
            elif IS_WINDOWS:
                print("    On Windows try: pip install pipwin && pipwin install pyaudio")

        print("\n>>> Trying to install pyvirtualcam (required for virtual webcam)...")
        r3 = subprocess.run([pip, "install", "pyvirtualcam"])
        if r3.returncode != 0:
            print("[!] pyvirtualcam not installed — virtual webcam won't work.")

    print(f"\n[OK] venv ready at {VENV_DIR}")


def _has_cmd(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def main():
    parser = argparse.ArgumentParser(description="PhoneCam — cross-platform installer")
    parser.add_argument("--skip-system", action="store_true",
                        help="Skip system package installation (requires sudo on Linux)")
    parser.add_argument("--audio-only", action="store_true",
                        help="Only install audio dependencies (portaudio/blackhole/vb-cable)")
    args = parser.parse_args()

    print("=" * 64)
    print("  PhoneCam — Installer")
    print(f"  Detected OS: {OS_NAME}")
    print("=" * 64)

    if not args.skip_system:
        if IS_LINUX:
            install_linux_system()
        elif IS_MACOS:
            install_macos_system()
        elif IS_WINDOWS:
            install_windows_system()
        else:
            print(f"[!] Unsupported OS: {OS_NAME}")
            sys.exit(1)

    setup_venv()

    print("\n" + "=" * 64)
    print(" Installation complete!")
    print("=" * 64)
    print()
    print("To start PhoneCam:")
    if IS_WINDOWS:
        print("  venv\\Scripts\\python.exe server.py")
        print("  or: run.bat")
    else:
        print("  ./run.py")
        print("  or: ./run.sh")
    print()
    print("To use phone microphone, add --audio:")
    print("  ./run.py --audio   (Linux/macOS)")
    print("  python server.py --audio   (Windows)")
    print()


if __name__ == "__main__":
    main()
