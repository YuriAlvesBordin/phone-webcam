# PhoneCam

Use your phone as a webcam for your PC. The phone only needs a browser:
video and audio travel over the local network via WebRTC (H.264 + Opus),
and the PC exposes them as regular camera and microphone devices that
Discord, OBS, Zoom, Meet, Teams and friends can pick up.

```
Phone (browser)                        PC (Linux/Windows/macOS)
┌──────────────────────┐   WebRTC     ┌─────────────────────────────┐
│ getUserMedia()       │ ───────────▶ │ aiortc decodes H.264/Opus   │
│ H.264 hw encode      │  (LAN only)  │ pyvirtualcam / virtual mic  │
└──────────────────────┘              └──────────────┬──────────────┘
                                                     ▼
                                    Discord / OBS / Zoom / Meet / Teams
```

Most phone-as-webcam tools stream JPEG frames over a plain WebSocket. It
works, but it wastes bandwidth and the delay creeps up during a call.
PhoneCam uses WebRTC end to end: the phone encodes with its hardware
H.264 encoder, the bitrate adapts to Wi-Fi conditions, and audio shares
the same connection as an Opus track.

> **Tested on Arch Linux.** Development and testing happen on Arch Linux
> (KDE Plasma, PipeWire). Windows and macOS are documented but untested;
> reports and patches for other platforms are welcome.

## Features

- H.264 hardware encoding on the phone: low bandwidth, stable latency
- Microphone over the same connection, with echo cancellation from the browser
- Native camera zoom when available, digital zoom fallback, front/back switch, torch
- Live stats (bitrate, FPS, RTT, packet loss) on a tap
- PIN authentication, HTTPS with a self-signed certificate, strict CSP
- Auto reconnect, wake lock, pause/resume
- Linux (v4l2loopback + PipeWire/PulseAudio), Windows (OBS Virtual Camera + VB-Cable), macOS (OBS Virtual Camera + BlackHole)
- Works on Android and iOS: any browser with WebRTC

## Requirements

PC:

- Python 3.10 to 3.13
- Linux: the `v4l2loopback` module for the virtual camera; PipeWire or PulseAudio for the virtual microphone (both preinstalled on most distros)
- Windows: [OBS Studio](https://obsproject.com/download) (open it once, click "Start Virtual Camera", close it); [VB-Cable](https://vb-audio.com/Cable/) for audio
- macOS: [OBS Studio](https://obsproject.com/download), same "Start Virtual Camera" step; [BlackHole 2ch](https://existential.audio/blackhole/) for audio

Phone: a browser with WebRTC, on the same Wi-Fi as the PC.

## Install

```bash
git clone https://github.com/YuriAlvesBordin/phone-webcam.git
cd phone-webcam

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Alternatively, `pip install .` inside the repository adds a `phonecam`
command to the virtualenv.

## Run

```bash
python run.py                     # or: python -m phonecam
```

The terminal prints a QR code and a 6-digit PIN. On the phone:

1. Connect to the same Wi-Fi as the PC
2. Open the URL (scan the QR code or type it)
3. Accept the self-signed certificate warning. This is expected, see [Security](#security)
4. Type the PIN shown in the terminal
5. Allow camera access

Then select the devices in the app you use:

| OS | Camera | Microphone |
|----|--------|------------|
| Linux | PhoneCam (`/dev/video10`) | PhoneCam Mic |
| Windows | OBS Virtual Camera | CABLE Output |
| macOS | OBS Virtual Camera | BlackHole 2ch |

## Options

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
  --no-https           # Disable HTTPS (only for localhost)
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

## Phone controls

| Action | How |
|--------|-----|
| Zoom | Slider, pinch on the video, mouse wheel on desktop |
| Pan while zoomed | Drag one finger (zoom above 1x) |
| Stats overlay | Tap the video |
| Switch camera | Flip button, or `F` on desktop |
| Pause | Pause button, or `Space` |
| Microphone | Mic button, or `M` |
| Torch | Torch button, or `T`, when the camera supports it |

## How it works

1. The PC runs a FastAPI server (HTTPS by default) with a WebSocket signaling endpoint.
2. The phone authenticates with the PIN, sent as the first WebSocket message, never in the URL.
3. SDP and ICE candidates are exchanged, and the WebRTC peer connection uses host candidates only (LAN, no STUN/TURN).
4. [aiortc](https://github.com/aiortc/aiortc) decodes the incoming tracks: H.264 frames go to [pyvirtualcam](https://github.com/tmo1/pyvirtualcam), Opus audio is decoded to mono PCM and written to the virtual microphone.
5. A watchdog closes sessions that stop sending frames for 15s. A new connection kicks the previous client. Stats go back to the phone every 2s.

Source layout:

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

## Security

Threat model: a trusted home LAN. This is not hardened against hostile
networks.

- HTTPS uses a self-signed certificate, so the browser will warn you. Check
  that the address is really your PC's IP before accepting.
- The 6-digit PIN authenticates the signaling handshake. Comparison is
  constant-time; 5 failed attempts lock the IP out for 60s.
- The QR code does not contain the PIN by default. `--qr-pin` embeds it for
  convenience, at the cost of security.
- Security headers: CSP, `X-Content-Type-Options`, `X-Frame-Options`. The
  Origin is validated on WebSocket upgrades and signaling messages are
  size limited.
- Nothing leaves the network: no STUN/TURN, no telemetry, nothing persisted.

## Troubleshooting

**No virtual camera on Linux.** Load the module with `exclusive_caps=1`
(Discord needs it):

```bash
sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10 card_label="PhoneCam"
v4l2-ctl --list-devices        # should list PhoneCam on /dev/video10
```

Package names: `v4l2loopback-dkms` on Arch and Debian/Ubuntu, `v4l2loopback`
on Fedora.

**No virtual camera on Windows/macOS.** Open OBS once, click "Start Virtual
Camera", close OBS.

**Discord does not list the camera (Linux).** Check that
`/sys/module/v4l2loopback/parameters/exclusive_caps` contains `1`. If not,
reload the module with the option set.

**No microphone on the PC.** Linux: install `pipewire` or
`pulseaudio-utils`. Windows: VB-Cable. macOS: BlackHole. You can test the
capture side at `https://<pc-ip>:8765/audio-test`, which records 3 seconds
from the virtual mic.

**Browser blocks the camera.** getUserMedia needs a secure context. Use the
`https://` URL and accept the certificate.

**Phone cannot open the URL.** Same Wi-Fi (guest networks usually isolate
clients) and check the firewall: Windows Defender, `sudo ufw allow 8765/tcp`,
macOS firewall.

**Stutter or lag.** Prefer 5GHz Wi-Fi, keep the phone close to the router,
and lower the ceiling: `--bitrate 2000` or `--width 960 --height 540`.

**Stream stops when the screen turns off.** The page holds a Wake Lock
(Chrome Android 84+, Safari iOS 16.4+). If the browser drops it, tap the
banner to re-enable. Reconnects use a backoff that goes from 1s up to 15s.

**Test the virtual camera.** Linux: `ffplay /dev/video10` or `cheese`.
Windows/macOS: the native Camera app or OBS.

## FAQ

**Does it work on iPhone?** Yes, Safari and Chrome on iOS support WebRTC.

**Can I use it over the internet?** No, the connection is LAN-only (host
candidates). Put phone and PC on the same VPN (WireGuard, Tailscale) and it
works as if they were local.

**What about battery?** Camera, Wi-Fi and hardware encoding drain it. Keep
the phone plugged in for long sessions.

**How much latency?** Usually under 150ms of video on 5GHz. Audio is lower.

## Development

```bash
pip install -e ".[dev]"
ruff check . && black --check .
pytest
```

## License

MIT. See [LICENSE](LICENSE).
