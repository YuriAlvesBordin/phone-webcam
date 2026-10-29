# PhoneCam

Use your phone as a webcam and microphone for your PC. The phone only needs a
browser: video and audio travel over the local network with WebRTC (H.264 +
Opus), and the PC exposes them as a regular camera and microphone that Discord,
OBS, Zoom, Meet, Teams and friends can pick up.

```
Phone (browser)                      PC (Linux)
┌──────────────────────┐   WebRTC   ┌─────────────────────────────────┐
│ getUserMedia()       │ ─────────▶ │ GStreamer webrtcbin             │
│ H.264 hw encode      │ (LAN only) │  ├─ H.264 -> v4l2loopback camera │
│ Opus mic             │            │  └─ Opus  -> PipeWire microphone │
└──────────────────────┘            └─────────────────────────────────┘
```

Version 3 is a rewrite in Rust (the Python/aiortc version is in the git history).
The phone web client and the signaling protocol are unchanged.

> **Tested on Arch Linux** (Hyprland, PipeWire). The Rust remaster targets
> Linux; the old Windows/macOS support relied on Python libraries.

## What changed in the rewrite

- **GStreamer `webrtcbin`** does ICE, DTLS, SRTP, NACK/PLI and the jitter buffer; decoding runs in native code instead of per-frame Python and NumPy copies.
- **The virtual camera never disappears.** The v4l2 and PipeWire outputs stay open while the service is on, so Discord and OBS keep the camera between reconnections (black picture while no phone is connected).
- **Letterboxing** instead of stretching when the phone sends another aspect ratio.
- **No open port while off.** The service starts disabled and only listens after `start`.
- **D-Bus service** (`dev.aqua.PhoneCam`) with live status: the aqua top bar shows an icon, a QR code, the PIN and stats, with a toggle.
- **Fixed UDP port range** (40000-40100) for firewalls, a certificate that regenerates when your IP changes, and `phonecam doctor`.
- One executable with the phone UI embedded.

## Install

Requirements: a Rust toolchain, GStreamer (tested with 1.28) with `gst-plugins-good`,
`gst-plugins-bad` (webrtcbin, libnice) and `gst-libav`, PipeWire with
`pipewire-pulse` (or PulseAudio), and `v4l2loopback` for the camera.

```sh
packaging/install.sh                 # user: binary + D-Bus service
sudo packaging/install-system.sh     # root, once: v4l2loopback with the right options
phonecam doctor                      # check everything
```

Details in [packaging/README.md](packaging/README.md).

## Use

```sh
phonecam serve        # foreground, QR code in the terminal
phonecam start        # or: turn it on in the aqua top bar
```

1. Connect the phone to the same Wi-Fi as the PC.
2. Scan the QR code (or type the URL) and accept the self-signed certificate warning. This is expected, see [Security](#security).
3. Type the 6-digit PIN and allow camera access.
4. Select **PhoneCam** as the camera and **PhoneCam Mic** as the microphone in your app.

Options (`phonecam serve --help`): `--port`, `--width`, `--height`, `--fps`,
`--bitrate`, `--pin`, `--qr-pin`, `--no-https`, `--no-audio`, `--device`.
Persistent settings go in `~/.config/phonecam/config.toml`.

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

1. The PC runs an HTTPS server (axum + rustls) with a WebSocket signaling endpoint.
2. The phone authenticates with the PIN, sent as the first WebSocket message, never in the URL.
3. SDP and ICE candidates are exchanged; the connection uses host candidates only (LAN, no STUN/TURN).
4. `webrtcbin` receives the tracks. Each track gets a depay → decoder chain that feeds `intervideosink`/`interaudiosink` channels.
5. Persistent output pipelines read those channels: video → `videoscale`/`videorate` → `v4l2sink`; audio → `pulsesink` into a null sink exposed as the **PhoneCam Mic** source.
6. A watchdog closes sessions that stop sending frames for 15 s. A new connection kicks the previous client. Stats go back to the phone every 2 s.

```
crates/
├── phonecam-core/   # config, security, TLS, signaling, WebRTC session, outputs, controller
│   └── tests/e2e.rs # fake phone (H.264 + Opus) over a real WebSocket
└── phonecam/        # CLI, D-Bus service, doctor
static/              # phone UI (HTML/CSS/ES modules), embedded in the binary
packaging/           # install scripts, D-Bus service, v4l2loopback config
```

## Security

Threat model: a trusted home LAN. This is not hardened against hostile networks.

- HTTPS with a self-signed certificate (stored with mode 0600), so the browser warns you. Check that the address is really your PC's IP before accepting.
- The 6-digit PIN authenticates the signaling handshake. Comparison is constant-time; 5 failed attempts lock the IP out for 60 s.
- The QR code does not contain the PIN unless you pass `--qr-pin`.
- Security headers: CSP, `X-Content-Type-Options`, `X-Frame-Options`. The Origin is validated on WebSocket upgrades and signaling messages are size limited.
- The server is off by default and closes its port when stopped. Nothing leaves the network: no STUN/TURN, no telemetry.

## Troubleshooting

Run `phonecam doctor` first.

**Discord does not list the camera.** `cat /sys/module/v4l2loopback/parameters/exclusive_caps` must print `Y` (or `1`). Reload the module with `exclusive_caps=1`.

**No microphone.** Needs `pipewire-pulse` (or PulseAudio): `pactl info` must work. The source is called "PhoneCam Mic".

**Phone cannot open the URL.** Same Wi-Fi (guest networks isolate clients), and open TCP 8765 and UDP 40000-40100 if you run a firewall.

**Browser blocks the camera.** `getUserMedia` needs a secure context: use the `https://` URL and accept the certificate.

**Stutter or lag.** Prefer 5 GHz Wi-Fi and lower the ceiling: `--bitrate 2000` or `--width 960 --height 540`.

**Test the virtual camera.** `ffplay /dev/video10` or `cheese`.

## Development

```sh
cargo test --workspace      # unit tests + end-to-end with a fake phone
cargo clippy --workspace --all-targets
```

## License

MIT. See [LICENSE](LICENSE).
