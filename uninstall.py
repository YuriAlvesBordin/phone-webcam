#!/usr/bin/env python3

import platform
import subprocess
from pathlib import Path

IS_LINUX = platform.system() == "Linux"
PROJECT_DIR = Path(__file__).parent.resolve()


def run(cmd, check=False):
    print(f">>> {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def uninstall_linux():
    print(">>> Removing v4l2loopback boot configs...")
    run(["sudo", "rm", "-f", "/etc/modprobe.d/phonecam.conf",
         "/etc/modules-load.d/phonecam.conf"], check=False)

    print(">>> Unloading v4l2loopback module...")
    r = run(["sudo", "modprobe", "-r", "v4l2loopback"])
    if r.returncode != 0:
        print("    (module in use — reboot to complete)")

    print(">>> Unloading 'phonecam_mic' null sink from PulseAudio...")
    r = run(["pactl", "list", "short", "modules"])
    if r.returncode == 0:
        for line in r.stdout.split("\n"):
            if "phonecam_mic" in line:
                module_id = line.split()[0]
                run(["pactl", "unload-module", module_id], check=False)


def uninstall_other():
    print(f">>> On {platform.system()}, no system configs to remove.")
    print("    Just remove the PhoneCam folder.")
    print("    Optional software to uninstall manually:")
    print("      - OBS Studio (if installed only for PhoneCam)")
    print("      - VB-Cable (Windows) or BlackHole (macOS) — if installed for microphone")


def cleanup_local():
    print()
    venv = PROJECT_DIR / "venv"
    certs = PROJECT_DIR / "certs"
    if venv.exists():
        ans = input(f"Remove {venv}? [y/N] ").strip().lower()
        if ans == "y":
            import shutil
            shutil.rmtree(venv)
            print(f"    [OK] {venv} removed")
    if certs.exists():
        ans = input(f"Remove {certs}? [y/N] ").strip().lower()
        if ans == "y":
            import shutil
            shutil.rmtree(certs)
            print(f"    [OK] {certs} removed")


def main():
    print("=" * 64)
    print("  PhoneCam — Uninstaller")
    print("=" * 64)
    print()
    if IS_LINUX:
        uninstall_linux()
    else:
        uninstall_other()
    print()
    print("[OK] PhoneCam uninstalled from system.")
    cleanup_local()


if __name__ == "__main__":
    main()
