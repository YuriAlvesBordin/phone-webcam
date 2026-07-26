#!/usr/bin/env python3

import platform
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
VENV_DIR = PROJECT_DIR / "venv"

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"


def get_venv_python() -> str:
    if IS_WINDOWS:
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python")


def get_venv_pip() -> str:
    if IS_WINDOWS:
        return str(VENV_DIR / "Scripts" / "pip.exe")
    return str(VENV_DIR / "bin" / "pip")


def ensure_venv():
    if VENV_DIR.exists():
        return

    print(">>> venv not found — running install.py first...")
    py = sys.executable
    try:
        subprocess.run([py, str(PROJECT_DIR / "install.py"), "--skip-system"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] install.py failed: {e}")
        sys.exit(1)


def ensure_v4l2loopback():
    if not IS_LINUX:
        return

    try:
        r = subprocess.run(["lsmod"], capture_output=True, text=True)
        if "v4l2loopback" in r.stdout:
            return
    except Exception:
        pass

    print(">>> Attempting to load v4l2loopback module...")
    try:
        r = subprocess.run(
            ["sudo", "modprobe", "v4l2loopback",
             "exclusive_caps=1", "video_nr=10", 'card_label=PhoneCam'],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("    [OK] /dev/video10 created")
        else:
            print(f"    [!] sudo modprobe failed: {r.stderr.strip() or r.stdout.strip()}")
            print("    Server will start anyway in debug mode.")
            print("    If virtual webcam fails, run manually:")
            print("      sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label=PhoneCam")
            print()
    except FileNotFoundError:
        print("[!] sudo not found — server will start in debug mode.")
        print("    If virtual webcam fails, load module manually:")
        print("      sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label=PhoneCam")
        print()


def main():
    ensure_venv()
    ensure_v4l2loopback()

    py = get_venv_python()
    cmd = [py, str(PROJECT_DIR / "server.py")] + sys.argv[1:]

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n[+] Shutting down PhoneCam...")


if __name__ == "__main__":
    main()
