#!/usr/bin/env bash
#
# Desinstalador PhoneCam — Linux/macOS.
# No Windows, desinstale manualmente removendo a pasta do PhoneCam.
#
# Remove:
#  - Configurações de boot do v4l2loopback (Linux)
#  - Módulo v4l2loopback (Linux, se não estiver em uso)
#  - Microfone virtual "phonecam_mic" no PulseAudio (Linux)
# NÃO remove:
#  - venv/ (Python deps) — rode: rm -rf venv/
#  - OBS Studio / VB-Cable / BlackHole (desinstale via Painel de Controle / Finder)
#
set -e

cd "$(dirname "$0")"

if [ "$(uname)" = "Linux" ]; then
    echo ">>> Removendo configurações de boot do v4l2loopback…"
    sudo rm -f /etc/modprobe.d/phonecam.conf /etc/modules-load.d/phonecam.conf 2>/dev/null || true

    echo ">>> Descarregando módulo v4l2loopback…"
    sudo modprobe -r v4l2loopback 2>/dev/null || echo "    (módulo em uso — reinicie o PC para concluir)"

    echo ">>> Descarregando null sink 'phonecam_mic' do PulseAudio…"
    pactl unload-module "module-null-sink" 2>/dev/null || true
    # Tenta pelo nome do sink (mais preciso)
    for module_id in $(pactl list short modules | grep "sink_name=phonecam_mic" | awk '{print $1}'); do
        pactl unload-module "$module_id" 2>/dev/null || true
    done
else
    echo ">>> Em $(uname), apenas remova a pasta do PhoneCam."
    echo "    No macOS, também:"
    echo "      - Desinstale o OBS Studio arrastando para o Trash (se instalado só para o PhoneCam)"
    echo "      - Desinstale o BlackHole (se instalado)"
fi

echo
echo "[OK] PhoneCam desinstalado do sistema."
echo "     Para remover também o venv Python: rm -rf venv/"
echo "     Para remover certificados SSL: rm -rf certs/"
