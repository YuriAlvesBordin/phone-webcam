#!/bin/sh
# Webcam virtual (precisa de root): instala o v4l2loopback e o carrega no boot com as opções certas.
# Arch: pacman. Em outras distros instale o pacote v4l2loopback (dkms) e copie v4l2loopback.conf.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Rode com sudo: sudo $0" >&2; exit 1; }
cd "$(dirname "$0")"
if command -v pacman >/dev/null 2>&1; then
    pacman -S --needed --noconfirm v4l2loopback-dkms
fi
install -Dm644 v4l2loopback.conf /etc/modprobe.d/phonecam-v4l2loopback.conf
echo v4l2loopback > /etc/modules-load.d/phonecam-v4l2loopback.conf
modprobe -r v4l2loopback 2>/dev/null || true
modprobe v4l2loopback
echo "Pronto: webcam virtual 'PhoneCam' em /dev/video10."
