# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-07-25

### Added
- Initial release of phone-webcam
- Use phone camera as virtual webcam on Windows, macOS, and Linux
- FastAPI + WebSocket backend with pyvirtualcam for virtual camera
- Mobile browser UI (static/index.html) with:
  - HD 1280×720 @ 30fps (configurable up to 1080p)
  - Smart crop (object-fit: cover) for portrait/landscape
  - Zoom 1×–4× with slider, buttons, pinch-to-zoom, and mouse scroll
  - Pan (drag) when zoomed
  - Front/back camera switch
  - Pause/resume stream
  - Real-time FPS counter
  - Wake Lock API to keep screen on
  - Auto-reconnection with exponential backoff
- Optional phone microphone as virtual microphone:
  - Linux: PulseAudio/PipeWire null sink + virtual source
  - macOS: BlackHole 2ch
  - Windows: VB-Cable
- Cross-platform installer (install.py) with OS detection:
  - Linux: v4l2loopback, v4l-utils, portaudio, python3-venv
  - macOS: portaudio via Homebrew
  - Windows: OBS Studio + VB-Cable guidance
- CLI launchers (run.py, run.sh, run.bat)
- PIN authentication (6 digits, random per run)
- HTTPS with auto-generated self-signed certificates
- QR code in terminal with embedded PIN for easy mobile connection
- Comprehensive README with installation, usage, troubleshooting
- MIT License
- GitHub Actions CI workflow (Linux/macOS/Windows, Python 3.10-3.13)
- Contributing guide, issue templates, code of conduct

### Security
- Random PIN per session
- Single-client enforcement (new connections disconnect old)
- Self-signed TLS for WebRTC getUserMedia requirement
- No persistent logs, PINs, or image storage