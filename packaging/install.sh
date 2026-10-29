#!/bin/sh
# Instalação por usuário: binário em ~/.local/bin e ativação por D-Bus (sem systemd, sem root).
# O módulo v4l2loopback precisa de root: veja install-system.sh.
set -eu
cd "$(dirname "$0")/.."
ROOT="${PREFIX:-$HOME/.local}"
cargo install --path crates/phonecam --root "$ROOT" --force
mkdir -p "$HOME/.local/share/dbus-1/services"
sed "s|@BIN@|$ROOT/bin/phonecam|" packaging/dev.aqua.PhoneCam.service.in \
    > "$HOME/.local/share/dbus-1/services/dev.aqua.PhoneCam.service"
echo "Instalado em $ROOT/bin/phonecam; serviço D-Bus dev.aqua.PhoneCam registrado."
echo "Rode 'phonecam doctor' para ver o que falta (webcam virtual: sudo packaging/install-system.sh)."
