#!/usr/bin/env python3
"""
PhoneCam — Desinstalador multiplataforma.

Remove:
  - Linux: configurações de boot do v4l2loopback + módulo + null sink do PulseAudio
  - macOS/Windows: apenas mostra instruções (não há nada a remover automaticamente)

NÃO remove:
  - venv/ (deps Python) — rode: python -c "import shutil; shutil.rmtree('venv')"
  - certs/ (certificados SSL) — rode: python -c "import shutil; shutil.rmtree('certs')"
  - OBS Studio / VB-Cable / BlackHole (desinstale manualmente)

Uso: python uninstall.py
"""

import platform
import subprocess
import sys
from pathlib import Path

IS_LINUX = platform.system() == "Linux"
PROJECT_DIR = Path(__file__).parent.resolve()


def run(cmd, check=False):
    print(f">>> {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def uninstall_linux():
    print(">>> Removendo configurações de boot do v4l2loopback…")
    run(["sudo", "rm", "-f", "/etc/modprobe.d/phonecam.conf",
         "/etc/modules-load.d/phonecam.conf"], check=False)

    print(">>> Descarregando módulo v4l2loopback…")
    r = run(["sudo", "modprobe", "-r", "v4l2loopback"])
    if r.returncode != 0:
        print("    (módulo em uso — reinicie o PC para concluir)")

    print(">>> Descarregando null sink 'phonecam_mic' do PulseAudio…")
    # Lista módulos e descarrega o null sink do phonecam
    r = run(["pactl", "list", "short", "modules"])
    if r.returncode == 0:
        for line in r.stdout.split("\n"):
            if "phonecam_mic" in line:
                module_id = line.split()[0]
                run(["pactl", "unload-module", module_id], check=False)


def uninstall_other():
    print(f">>> Em {platform.system()}, não há configurações de sistema para remover.")
    print("    Apenas remova a pasta do PhoneCam.")
    print("    Softwares opcionais a desinstalar manualmente:")
    print("      - OBS Studio (se instalado só para o PhoneCam)")
    print("      - VB-Cable (Windows) ou BlackHole (macOS) — se instalou para microfone")


def cleanup_local():
    """Remove venv e certs locais (opcional, pergunta antes)."""
    print()
    venv = PROJECT_DIR / "venv"
    certs = PROJECT_DIR / "certs"
    if venv.exists():
        ans = input(f"Remover {venv}? [y/N] ").strip().lower()
        if ans == "y":
            import shutil
            shutil.rmtree(venv)
            print(f"    [OK] {venv} removido")
    if certs.exists():
        ans = input(f"Remover {certs}? [y/N] ").strip().lower()
        if ans == "y":
            import shutil
            shutil.rmtree(certs)
            print(f"    [OK] {certs} removido")


def main():
    print("=" * 64)
    print("  PhoneCam — Desinstalador")
    print("=" * 64)
    print()
    if IS_LINUX:
        uninstall_linux()
    else:
        uninstall_other()
    print()
    print("[OK] PhoneCam desinstalado do sistema.")
    cleanup_local()


if __name__ == "__main__":
    main()
