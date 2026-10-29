# Installing PhoneCam

## 1. User install (no root)

```sh
packaging/install.sh
```

Builds and installs `~/.local/bin/phonecam` and registers the D-Bus service
`dev.aqua.PhoneCam` (`~/.local/share/dbus-1/services/`). The daemon starts on
demand (the first `phonecam start`, or the aqua top bar toggle) and exits by
itself 90 s after being turned off. If the session bus does not see the new
service file yet, reload it once:

```sh
busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig
```

## 2. Virtual webcam (root, once)

```sh
sudo packaging/install-system.sh
```

Installs `v4l2loopback-dkms` (Arch), loads it at boot with `exclusive_caps=1
video_nr=10 card_label="PhoneCam"` and loads it now. `exclusive_caps=1` is
required for Discord and Chrome to list the camera. Without the module the
service still runs: the microphone works and the status shows a warning.

Other distros: install the `v4l2loopback` package and copy
`v4l2loopback.conf` to `/etc/modprobe.d/`.

## 3. Check

```sh
phonecam doctor
```

## Commands

| Command | What it does |
|---------|--------------|
| `phonecam serve` | Foreground server with QR code in the terminal (standalone use) |
| `phonecam start` / `stop` / `toggle` | Control the D-Bus service |
| `phonecam status` | State as JSON |
| `phonecam pin` | New PIN without disconnecting the phone |
| `phonecam disconnect` | Drop the connected phone |
| `phonecam doctor` | List what is missing on the system |

## D-Bus API

Name `dev.aqua.PhoneCam`, path `/dev/aqua/PhoneCam`, interface
`dev.aqua.PhoneCam1`.

Methods: `Start() -> s`, `Stop() -> s`, `Toggle() -> s`, `GetStatus() -> s`,
`RegeneratePin() -> s`, `Disconnect()`. Signal: `Changed(s)`. The string is the
JSON of `Status` (see `crates/phonecam-core/src/status.rs`); new fields may be
added, consumers must ignore unknown ones.

## Ports

TCP 8765 (page and signaling), UDP 40000-40100 (WebRTC media). Open them if a
firewall is active.

## Config

`~/.config/phonecam/config.toml` (all keys optional):

```toml
port = 8765
width = 1280
height = 720
fps = 30
bitrate_kbps = 4000
audio = true
https = true
# pin = "123456"      # fixed PIN; default is random on each start
# video_device = "/dev/video10"
```
