#!/usr/bin/env python3
"""Convenience launcher: loads the v4l2loopback kernel module (Linux) and
starts PhoneCam in the current Python environment."""

import subprocess
import sys


def ensure_v4l2loopback() -> None:
    if sys.platform != "linux":
        return
    try:
        r = subprocess.run(["lsmod"], capture_output=True, text=True)
        if "v4l2loopback" in r.stdout:
            return
    except Exception:
        pass

    print(">>> Attempting to load the v4l2loopback module...")
    try:
        r = subprocess.run(
            [
                "sudo",
                "modprobe",
                "v4l2loopback",
                "exclusive_caps=1",
                "video_nr=10",
                "card_label=PhoneCam",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            print("    [OK] /dev/video10 created")
        else:
            print(f"    [!] modprobe failed: {r.stderr.strip() or r.stdout.strip()}")
            print("    If the virtual webcam is missing, load it manually:")
            print(
                "      sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label=PhoneCam"
            )
    except FileNotFoundError:
        print("[!] sudo not found - load the module manually if needed:")
        print("    sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label=PhoneCam")


def main() -> None:
    try:
        from phonecam.cli import main as cli_main
    except ImportError:
        print("[ERROR] PhoneCam dependencies are missing. Install them with:")
        print()
        print("    python -m venv venv")
        print("    venv/bin/pip install -r requirements.txt   (Windows: venv\\Scripts\\pip)")
        print()
        sys.exit(1)

    ensure_v4l2loopback()
    cli_main()


if __name__ == "__main__":
    main()
