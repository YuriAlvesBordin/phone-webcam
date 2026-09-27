# PhoneCam

Turn your phone into a high-quality **virtual webcam** for your PC — over Wi-Fi, with **WebRTC** (H.264 video + Opus audio), zero installs on the phone, one command on the PC.

```
Phone (browser)                         PC (Win/Mac/Linux)
┌──────────────────────┐   WebRTC      ┌─────────────────────────────┐
│ getUserMedia()       │ ────────────▶ │ aiortc decodes H.264/Opus   │
│ H.264 hw encode      │  (LAN only)   │ pyvirtualcam / virtual mic  │
└──────────────────────┘               └──────────────┬──────────────┘
                                                      ▼
                                     Discord / OBS / Zoom / Meet / Teams
```

## ✨ Features

- 🎥 **H.264 hardware encoding** on the phone — ~5x less bandwidth than MJPEG webcam apps, much lower latency
- 🎤 **Audio + video on one connection** — the phone mic becomes a virtual microphone (echo cancellation included)
- 📶 **Adaptive bitrate** — WebRTC congestion control adjusts quality in real time
- 🔍 **Native camera zoom** (when the camera exposes it), pinch-to-zoom, drag-to-pan, front/back switch, 🔦 torch
- 📊 **Live stats overlay** (bitrate, FPS, RTT, packet loss) — tap the image
- 🔒 **PIN authentication** (constant-time compare, rate limiting), HTTPS by default, strict CSP
- 🔄 Auto-reconnect, wake lock, pause/resume
- 🌐 Cross-platform: Linux (v4l2loopback + PipeWire/PulseAudio), Windows (OBS Virtual Camera + VB-Cable), macOS (OBS Virtual Camera + BlackHole)
- 📱 Works on Android and iPhone — any browser with WebRTC (Chrome, Safari, Firefox, Edge)

## 📦 Requirements

**PC:**
- Python 3.10 – 3.13
- Linux: `v4l2loopback` module; audio needs PipeWire or PulseAudio (preinstalled on most distros)
- Windows: [OBS Studio](https://obsproject.com/download) (open it once, click "Start Virtual Camera", close it); audio needs [VB-Cable](https://vb-audio.com/Cable/)
- macOS: [OBS Studio](https://obsproject.com/download) (same "Start Virtual Camera" once); audio needs [BlackHole 2ch](https://existential.audio/blackhole/)

**Phone:** modern browser, same Wi-Fi as the PC.

## 🚀 Install

```bash
git clone https://github.com/YuriAlvesBordin/phone-webcam.git
cd phone-webcam

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Or as a package (`pip install .` adds the `phonecam` command).

## ▶️ Run

```bash
python run.py                     # or: python -m phonecam
```

The terminal shows a **QR code** and a **6-digit PIN**. On the phone:

1. Connect to the **same Wi-Fi** as the PC
2. Scan the QR code (or open the URL shown in the terminal)
3. Accept the self-signed certificate warning (expected — see [Security](#-security))
4. Enter the PIN displayed in the PC terminal
5. Allow camera access — you are live

Then select the camera and mic in your app:

| OS | Camera in Discord/OBS/Zoom | Microphone |
|----|---------------------------|------------|
| Linux | **PhoneCam** (`/dev/video10`) | **PhoneCam Mic** |
| Windows | **OBS Virtual Camera** | **CABLE Output** |
| macOS | **OBS Virtual Camera** | **BlackHole 2ch** |

## ⚙️ Options

```bash
python run.py [options]

  --host 0.0.0.0       # Bind address
  --port 8765          # TCP port
  --width 1280         # Virtual camera resolution
  --height 720
  --fps 30             # Target FPS
  --bitrate 4000       # Max video bitrate (kbps)
  --pin 123456         # Fixed PIN (default: random each run)
  --qr-pin             # Embed PIN in the QR URL (auto-connect; less secure)
  --no-https           # Disable HTTPS (ONLY for localhost)
  --no-audio           # Disable phone microphone
  -v                   # Verbose (debug) logging
```

Examples:

```bash
python run.py --width 1920 --height 1080 --fps 60 --bitrate 8000   # 1080p60
python run.py --no-audio                                           # video only
python run.py --pin 424242 --port 9000                             # automation
python run.py --width 640 --height 480 --bitrate 1500              # weak Wi-Fi
```

## 📱 Phone controls

| Action | How |
|--------|-----|
| Zoom | Slider, −/+ buttons, **pinch** on the video, mouse wheel (desktop) |
| Move zoomed area | Drag one finger (zoom > 1×) |
| Stats overlay | **Tap** the image (bitrate, FPS, RTT, loss) |
| Switch camera | 🔄 button (or `F` on desktop) |
| Pause | ⏸ button (`Space`) |
| Microphone | 🎤 ON/OFF (`M`) |
| Torch | 🔦 button, when the camera supports it (`T`) |

## 🧱 How it works

1. The PC runs a FastAPI server (HTTPS) with a WebSocket **signaling** endpoint
2. The phone authenticates with the PIN — sent as the **first message**, never in the URL
3. SDP/ICE exchange establishes a **WebRTC** peer connection (host candidates — LAN only, no STUN/TURN)
4. [aiortc](https://github.com/aiortc/aiortc) decodes H.264 → frames go to [pyvirtualcam](https://github.com/tmo1/pyvirtualcam); Opus → PCM mono goes to the virtual microphone
5. A watchdog closes dead sessions (15s without frames); a new phone kicks the previous one; stats flow back every 2s

```
phonecam/
├── cli.py          # arguments, banner, uvicorn startup
├── config.py       # Config dataclass + named constants
├── security.py     # PIN (constant-time), rate limiter, Origin check
├── signaling.py    # WebSocket signaling protocol
├── webrtc.py       # aiortc session: tracks, watchdog, stats
├── virtualcam.py   # pyvirtualcam wrapper + health check
├── audio/          # virtual mic sinks (PipeWire/PulseAudio, BlackHole, VB-Cable)
└── server.py       # FastAPI app, routes, security headers
static/             # phone UI (HTML/CSS/ES modules)
```

## 🔒 Security

Threat model: **trusted home LAN**. PhoneCam is not hardened for hostile networks.

- HTTPS with a **self-signed certificate** — the browser warning is expected. Verify the address matches your PC's IP before accepting; a local MITM could otherwise intercept traffic
- The **PIN** (6 digits, `secrets`) authenticates the signaling handshake; 5 failed attempts lock an IP out for 60s; comparison is constant-time
- By default the QR code contains **no PIN** (scan + type). `--qr-pin` trades security for convenience
- Strict **CSP**, `X-Content-Type-Options`, `X-Frame-Options`, Origin validation on WebSocket upgrades, signaling message size limits
- No data leaves your network: no STUN/TURN, no telemetry, nothing persisted

## 🛠 Troubleshooting

**Virtual webcam missing (Linux)** — load the module (Discord requires `exclusive_caps=1`):
```bash
sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label="PhoneCam"
v4l2-ctl --list-devices        # should show PhoneCam → /dev/video10
```
Install the module: `sudo pacman -S v4l2loopback-dkms` (Arch), `sudo apt install v4l2loopback-dkms` (Ubuntu/Debian), `sudo dnf install v4l2loopback` (Fedora).

**Windows/macOS: camera missing** — open OBS once, click "Start Virtual Camera", close OBS.

**Discord doesn't list the camera (Linux)** — check `cat /sys/module/v4l2loopback/parameters/exclusive_caps` prints `1`; if not, reload the module with `exclusive_caps=1`.

**No microphone on the PC** — Linux: install `pipewire` or `pulseaudio-utils`; Windows: [VB-Cable](https://vb-audio.com/Cable/); macOS: [BlackHole](https://existential.audio/blackhole/). Test with `https://<pc-ip>:8765/audio-test` (records 3s from the virtual mic).

**Browser blocks the camera** — getUserMedia requires HTTPS: use the `https://` URL and accept the certificate.

**Phone can't reach the URL** — same Wi-Fi (not an isolated guest network)? Firewall: Windows Defender / `sudo ufw allow 8765/tcp` / macOS firewall.

**Stutter or lag** — use 5GHz Wi-Fi, keep the phone near the router, and lower the ceiling: `--bitrate 2000` or `--width 960 --height 540`.

**Screen off stops the stream** — the page requests a Wake Lock (Chrome Android 84+, Safari iOS 16.4+); when lost, tap the yellow hint to re-enable. Auto-reconnect covers drops: 1s → 2s → 2s → 5s → 5s → 10s → 10s → 15s.

**Verify the camera works** — Linux: `ffplay /dev/video10` or `cheese --device /dev/video10`; Windows/macOS: native Camera app or OBS.

## ❓ FAQ

**iPhone?** Yes — Safari or Chrome iOS with WebRTC.

**Remote use over 4G/VPN?** WebRTC here is LAN-only (host candidates). For remote use, put phone and PC on the same VPN (WireGuard/Tailscale) — host candidates will still match.

**Battery?** Camera + Wi-Fi + H.264 encode consume power; keep the phone plugged in for long sessions.

**Latency?** Typically well under 150ms video on 5GHz; audio lower. H.264 + WebRTC keeps it stable where MJPEG-over-WebSocket accumulated delay.

## 💻 Development

```bash
pip install -e ".[dev]"
ruff check . && black --check .
```

## 📜 License

MIT — see [LICENSE](LICENSE).
