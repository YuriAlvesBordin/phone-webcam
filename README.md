# PhoneCam

Use your **phone's camera as a virtual webcam on your PC** — works on **Windows, macOS, and Linux**. Compatible with Discord, OBS Studio, Zoom, Google Meet, Teams, Cheese, and any app that accepts webcam devices.

No native app on the phone: everything runs in the browser (Chrome, Safari, Firefox). Works on **Android and iPhone**.

**Optional** support for phone microphone as virtual microphone on PC.

---

## ✨ Features

- 🌐 **Cross-platform** — Windows 10+, macOS 11+, Ubuntu/Debian, Fedora, Arch Linux
- 📱 **No app on phone** — opens a page in the browser
- 🍎 **Android + iPhone** — uses standard Web `getUserMedia`
- 🎥 **HD 1280×720 @ 30fps** (configurable up to 1080p)
- 📐 **No distortion** — smart crop (object-fit: cover) works in portrait and landscape
- 🔍 **Zoom 1×–4×** — slider, +/− buttons, **pinch-to-zoom** (natural gesture), and mouse scroll
- ✋ **Pan** — drag finger over video when zoomed
- 🎤 **Phone microphone** — uses phone mic as virtual mic on PC (optional, `--audio`)
- 🔒 **6-digit PIN** — no one on the network connects without authorization
- 📲 **QR Code in terminal** — scan directly from PC with phone camera
- 💡 **Wake Lock** — keeps phone screen on while streaming
- 🔄 **Auto-reconnect** — recovers from Wi-Fi drops or screen off
- 🔄 **Front/back camera switch** with one tap
- ⏸ **Pause/resume** stream without disconnecting
- 📊 **Real-time FPS** on phone and PC
- 🔌 **Virtual webcam** — via v4l2loopback (Linux) or OBS Virtual Camera (Windows/macOS)
- 💻 **Pure CLI** — no heavy GUI

---

## 🏗 Architecture

```
   Phone (browser)                        PC (Win/Mac/Linux)
  ┌─────────────────────┐                ┌──────────────────────────┐
  │  getUserMedia()     │                │  FastAPI + uvicorn       │
  │  canvas.toBlob(JPEG)│ ──WebSocket──▶ │  /ws (binary frames)     │
  │  ws.send(jpeg)      │                │  Pillow decodes          │
  └─────────────────────┘                │  pyvirtualcam.send(BGR)  │
                                          │  → /dev/video10 (Linux)  │
                                          │  → OBS Virtual Cam (Win) │
                                          │  → OBS Virtual Cam (Mac) │
                                          └────────┬─────────────────┘
                                                   │
                                          Discord / OBS / Zoom ──▶ virtual webcam
```

**Backends per OS**:

| OS | Virtual Webcam | Virtual Microphone (optional) |
|----|----------------|-------------------------------|
| **Linux** | `v4l2loopback` (auto-installed) | PulseAudio/PipeWire null sink (auto-created) |
| **Windows** | OBS Virtual Camera (install OBS Studio) | VB-Cable (manual install) |
| **macOS** | OBS Virtual Camera (install OBS Studio) | BlackHole (manual install) |

---

## 📦 Installation

### Prerequisites

- **Python 3.10+** installed (https://python.org)
- Phone and PC on **same Wi-Fi network**
- Modern browser on phone (Chrome 90+, Safari 14+, Firefox 88+)
- **On Windows/macOS**: install [OBS Studio](https://obsproject.com/download) and enable "Virtual Camera" once (for virtual webcam to work)
- **For phone microphone (optional)**:
  - Linux: nothing extra (PulseAudio/PipeWire already installed)
  - Windows: install [VB-Cable](https://vb-audio.com/Cable/)
  - macOS: install [BlackHole](https://existential.audio/blackhole/)

### Windows

```cmd
:: 1) Extract phone-webcam to any folder
cd phone-webcam

:: 2) Install (detects everything automatically)
install.bat

:: 3) Run
run.bat
```

### macOS and Linux

```bash
# 1) Extract phone-webcam to any folder
cd phone-webcam

# 2) Make scripts executable (first time only)
chmod +x install.sh run.sh install.py run.py

# 3) Install
./install.sh

# 4) Run
./run.sh
```

### Alternative installation (any OS, via Python directly)

```bash
python install.py        # installs
python run.py            # runs
```

---

## 🚀 Usage

### Start

```bash
# Linux/macOS
./run.sh

# Windows
run.bat

# Any OS (via Python directly)
python run.py
```

Terminal will show something like:

```
================================================================
  PhoneCam — Phone as Webcam on PC
================================================================

  OS        : Linux
  Resolution: 1280x720 @ 30fps
  PIN       : 482910
  Protocol  : HTTPS
  Cert SSL  : /home/.../phonecam/certs/phonecam.pem
  Microphone: disabled (use --audio to enable)

  ┌─ Scan QR Code with phone camera ─┐
  │  (or use http://... above)       │
  └──────────────────────────────────┘

      █▀▀▀▀▀█ ▄▄  ▄ ▀▄█ █▀▀▀▀▀█
      █ ███ █ █▄██▄█ ▀▄ █ ███ █
      ... (QR Code rendered as ASCII art)

  URL embedded in QR: https://192.168.1.42:8765/

  ⚠  CERTIFICATE WARNING: browser will show 'Connection not
     secure'. This is NORMAL — accept to continue.
```

> **Why HTTPS?** Browsers only allow `getUserMedia` (camera access) in secure contexts (HTTPS or `localhost`). PhoneCam auto-generates a self-signed SSL certificate in `certs/` on first run.

### On Phone

1. Connect to **same Wi-Fi network** as PC
2. **Scan QR Code** shown in PC terminal with phone camera — PIN is already embedded in URL, so page connects **automatically** without typing PIN
3. **Accept certificate warning**:
   - **Chrome Android**: "Your connection is not private" → "Advanced" → "Proceed to 192.168.x.x (unsafe)"
   - **Safari iOS**: "This site is not secure" → "Show Details" → "Visit this website"
   - **Firefox Android**: "Potential security risk" → "Advanced" → "Accept risk and continue"
4. If you didn't scan QR, open `https://<pc-ip>:8765/?pin=XXXXXX` manually (or type PIN on screen)
5. Allow camera access when prompted
6. Done — camera is streaming

### In Apps (Discord, OBS, etc.)

| App | How to Select Webcam |
|-----|----------------------|
| **Discord** | Settings → Voice & Video → Video Device → **OBS Virtual Camera** (Win/Mac) or **PhoneCam** (Linux) |
| **OBS** | + in Sources → Video Capture Device → **OBS Virtual Camera** or **PhoneCam** |
| **Zoom** | Settings → Video → Camera → **OBS Virtual Camera** or **PhoneCam** |
| **Google Meet / Teams** | Settings → Video → **OBS Virtual Camera** or **PhoneCam** |

### Phone Controls

| Action | How |
|--------|-----|
| **Zoom in/out** | Slider, **−** / **+** buttons, or **pinch** with 2 fingers on video |
| **Reset zoom** | **1.0×** button (bottom right) |
| **Move zoomed area** | Drag 1 finger over video (only works with zoom > 1×) |
| **Switch camera** | **🔄 Switch camera** button |
| **Pause stream** | **⏸ Pause** button |
| **Microphone** | **🎤 Microphone OFF/ON/MUTE** button (only if `--audio` enabled) |

---

## ⚙️ Command Line Options

```bash
./run.sh [options]        # Linux/macOS
run.bat [options]         # Windows
python run.py [options]   # any OS

Options:
  --host 0.0.0.0       # Bind (default: 0.0.0.0 = all interfaces)
  --port 8765          # TCP port (default: 8765)
  --width 1280         # Width (default: 1280)
  --height 720         # Height (default: 720)
  --fps 30             # Target FPS (default: 30)
  --pin 123456         # Fixed PIN (default: random each run)
  --no-https           # Disable HTTPS (ONLY for localhost; phones require HTTPS)
  --no-audio           # Disable phone microphone (audio is ON by default)
```

### Examples

```bash
# Default: HD video + HTTPS + QR Code + microphone
./run.sh

# Video only (no phone microphone)
./run.sh --no-audio

# Full HD 1080p
./run.sh --width 1920 --height 1080

# Different port + fixed PIN (useful for automation)
./run.sh --port 9000 --pin 246810

# SD 480p (weak Wi-Fi)
./run.sh --width 640 --height 480 --fps 24

# Test locally without HTTPS (only this PC, no phone)
./run.sh --no-https --host 127.0.0.1
```

---

## 🎤 Using Phone Microphone

By default, PhoneCam enables **video + microphone**. For video only, use `--no-audio`.

### Prerequisites per OS

| OS | Required Software | How to Install |
|----|-------------------|----------------|
| **Linux** | None extra | PulseAudio/PipeWire already installed |
| **Windows** | [VB-Cable](https://vb-audio.com/Cable/) | Download and run `VBCABLE_Setup_x64.exe` as admin, reboot PC |
| **macOS** | [BlackHole 2ch](https://existential.audio/blackhole/) | Download .pkg and install |

### On Phone

After connecting (PIN + camera), tap **🎤 Microphone OFF**:

- **1st tap**: enables microphone (button turns green "🎤 Microphone ON")
- **2nd tap**: mutes (button becomes "🔇 Microphone MUTE")
- **3rd tap**: disables everything (back to "🎤 Microphone OFF")

### In Apps, Select Microphone

| OS | Device Name in Discord/OBS/Zoom |
|----|---------------------------------|
| **Linux** | **Monitor of PhoneCam Mic** (or "PhoneCam Mic") |
| **Windows** | **CABLE Output** (VB-Audio Virtual Cable) |
| **macOS** | **BlackHole 2ch** |

---

## 🛠 Troubleshooting

### "malloc(): invalid size (unsorted)" on Linux (PyAudio crash)

Known bug in **pyaudio 0.2.14 with Python 3.14 + PipeWire** on Linux. PhoneCam has **automatic fallback** to `paplay` (PulseAudio CLI):

1. If PyAudio crashes on start, server automatically uses `paplay` as subprocess
2. You'll see in log: `[OK] Virtual microphone created: 'PhoneCam Mic' (via paplay CLI)`
3. Audio flows: phone → WS → paplay stdin → null sink → Discord/OBS

`paplay` is part of `libpulse` (already installed on most distros). If missing:

```bash
sudo pacman -S libpulse          # Arch
sudo apt install pulseaudio-utils  # Ubuntu/Debian
sudo dnf install pulseaudio-utils  # Fedora
```

### "ALSA lib pcm.c: Unknown PCM cards.pcm.rear" spam

Harmless. PhoneCam now **auto-silences** these warnings via `snd_lib_error_set_handler` in ALSA. If they still appear, ignore — they don't affect functionality.

### "Could not create virtual webcam"

| OS | Solution |
|----|----------|
| **Linux** | `sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label="PhoneCam"` |
| **Windows** | Install [OBS Studio](https://obsproject.com/download), open once and enable "Start Virtual Camera" |
| **macOS** | Install [OBS Studio](https://obsproject.com/download), open once and enable "Virtual Camera" |

### "Camera won't open on phone" / `getUserMedia undefined`

Cause: browser blocks camera via remote HTTP. **Solution**: use `https://` (with self-signed cert — accept warning). PhoneCam enables HTTPS automatically.

### "Screen turned off and camera stopped"

PhoneCam has two defenses:
1. **Wake Lock API** — tries to keep screen on (Chrome Android 84+, Safari iOS 16.4+)
2. **Auto-reconnect** — if connection drops, retries with exponential backoff: 1s → 2s → 2s → 5s → 5s → 10s → 10s → 15s

Tips:
- Keep phone plugged in
- Use Chrome Android for best Wake Lock support
- Disable browser battery optimization for the site

### Discord doesn't show camera (Linux)

Discord requires `exclusive_caps=1` on v4l2loopback. Check:

```bash
cat /sys/module/v4l2loopback/parameters/exclusive_caps
# Must print: 1
```

If it prints `0`, unload and reload:

```bash
sudo modprobe -r v4l2loopback
sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label="PhoneCam"
```

### "incompatible constructor arguments" (pyvirtualcam)

`server.py` does signature introspection via `inspect.signature()` and builds the correct call automatically. If it still fails:

```bash
# Linux/macOS
source venv/bin/activate
pip install --upgrade pyvirtualcam

# Windows
venv\Scripts\activate
pip install --upgrade pyvirtualcam
```

### Phone can't access URL

1. Confirm PC and phone are on **same Wi-Fi** (not isolated "Guest" networks)
2. Check firewall:
   - **Windows**: allow port 8765 in Windows Defender Firewall
   - **Linux**: `sudo ufw allow 8765/tcp` (if using UFW)
   - **macOS**: System Settings → Network → Firewall → allow
3. Test with `ping <pc-ip>` from phone

### Lag / Low FPS

- Reduce quality: `./run.sh --width 640 --height 480 --fps 24`
- Use 5GHz Wi-Fi instead of 2.4GHz
- Close other network apps on phone
- Keep phone close to router

### List Video Devices (Linux)

```bash
v4l2-ctl --list-devices
# Should show:
# PhoneCam (platform:v4l2loopback-000):
#   /dev/video10
```

### Verify Camera Works

- **Linux**: `ffplay /dev/video10` or `cheese --device /dev/video10`
- **Windows/macOS**: open native Camera app or OBS Studio and select "OBS Virtual Camera"

### List Audio Devices

```bash
# Linux
pactl list short sources    # shows "Monitor of PhoneCam Mic"

# Windows (PowerShell)
# Open Control Panel → Sound → Recording → should show "CABLE Output"

# macOS
# System Settings → Sound → Input → should show "BlackHole 2ch"
```

---

## 🗑 Uninstall

### Linux

```bash
./uninstall.sh           # removes v4l2loopback module + configs
rm -rf venv/             # removes Python deps
```

### Windows / macOS

```bash
# Remove venv
rm -rf venv/             # macOS/Linux
rmdir /s /q venv         # Windows (cmd)

# To remove OBS Studio / VB-Cable / BlackHole, uninstall via Control Panel (Windows)
# or drag app to Trash (macOS).
```

---

## 🔒 Security

- PIN is **random per run** (use `--pin` to fix)
- Server listens on `0.0.0.0` — accessible to any device on LAN
- **No TLS with trusted CA** — frames travel with self-signed cert. For corporate/shared networks, consider SSH tunnel or add your own CA
- Only **one phone connects at a time** (new connections drop previous)
- No persistence of PIN, logs, or images — everything is ephemeral

---

## 📁 Project Structure

```
phonecam/
├── server.py              # FastAPI + WebSocket + virtual cam/mic
├── static/
│   └── index.html         # Mobile page (getUserMedia + WS)
├── requirements.txt       # Python dependencies
├── install.py             # Cross-platform installer (Python)
├── install.sh             # Bash wrapper for install.py (Linux/macOS)
├── install.bat            # CMD wrapper for install.py (Windows)
├── run.py                 # Cross-platform launcher (Python)
├── run.sh                 # Bash wrapper for run.py (Linux/macOS)
├── run.bat                # CMD wrapper for run.py (Windows)
├── uninstall.sh           # Removes v4l2loopback module (Linux only)
└── README.md              # This file
```

---

## ❓ FAQ

**Works with iPhone?**  
Yes, via Safari 14+ or Chrome iOS. Page uses standard Web APIs.

**Works on Windows 11?**  
Yes, tested on Windows 10 and 11. Requires OBS Studio installed for virtual webcam.

**Works on Mac with Apple Silicon (M1/M2/M3)?**  
Yes, native support. Install OBS Studio for Apple Silicon.

**Can I use 4G instead of Wi-Fi?**  
Not directly. Server only listens on local network. For remote use, set up a VPN (WireGuard/Tailscale) between phone and PC.

**Consumes lots of phone battery?**  
Yes — camera + Wi-Fi + continuous JPEG encoding. Recommended: phone plugged in for long sessions.

**Typical latency?**  
- Video: 80-180ms on 5GHz Wi-Fi
- Audio: 50-120ms
- On 2.4GHz Wi-Fi can reach 300ms+

**How does wake lock work?**  
Page uses `navigator.wakeLock.request("screen")` to ask OS to keep screen on. Supported in Chrome Android 84+ and Safari iOS 16.4+.

---

## 📜 License

MIT — use freely. Attribution appreciated but not required.