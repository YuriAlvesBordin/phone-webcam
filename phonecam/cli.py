"""Command-line entry point: argument parsing, banner and server startup."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from phonecam import __version__
from phonecam.config import (
    DEFAULT_BITRATE_KBPS,
    DEFAULT_FPS,
    DEFAULT_HEIGHT,
    DEFAULT_PORT,
    DEFAULT_WIDTH,
    MAX_SIGNAL_MSG_BYTES,
    Config,
)
from phonecam.dependencies import HAS_QRCODE
from phonecam.log import setup as setup_logging
from phonecam.net import get_local_ips
from phonecam.qr import qr_ascii
from phonecam.security import generate_pin
from phonecam.server import BASE_DIR, create_app
from phonecam.sslcert import ensure_ssl_cert
from phonecam.virtualcam import OS_NAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phonecam",
        description="PhoneCam - use your phone as a PC webcam (WebRTC)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH, help=f"Width (default: {DEFAULT_WIDTH})"
    )
    parser.add_argument(
        "--height", type=int, default=DEFAULT_HEIGHT, help=f"Height (default: {DEFAULT_HEIGHT})"
    )
    parser.add_argument(
        "--fps", type=int, default=DEFAULT_FPS, help=f"Target FPS (default: {DEFAULT_FPS})"
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=DEFAULT_BITRATE_KBPS,
        help=f"Max video bitrate in kbps (default: {DEFAULT_BITRATE_KBPS})",
    )
    parser.add_argument(
        "--pin", type=str, default=None, help="Fixed PIN (default: random each run)"
    )
    parser.add_argument(
        "--qr-pin",
        action="store_true",
        help="Embed the PIN in the QR code URL (convenient, less secure)",
    )
    parser.add_argument(
        "--no-https",
        action="store_true",
        help="Disable HTTPS (ONLY for localhost; phones require HTTPS)",
    )
    parser.add_argument(
        "--no-audio", action="store_true", help="Disable phone microphone (audio is ON by default)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose (debug) logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def banner(cfg: Config, pin: str, cert_file: Path | None = None) -> None:
    ips = get_local_ips()
    scheme = "https" if cfg.https else "http"
    print()
    print("=" * 64)
    print(f"  PhoneCam v{__version__} - Phone as Webcam on PC (WebRTC)")
    print("=" * 64)
    print()
    print(f"  OS        : {OS_NAME}")
    print(
        f"  Video     : {cfg.width}x{cfg.height} @ {cfg.fps}fps "
        f"(H.264, max {cfg.bitrate_kbps} kbps)"
    )
    print(f"  PIN       : {pin}")
    print(f"  Protocol  : {scheme.upper()}")
    if cert_file and cfg.https:
        print(f"  Cert SSL  : {cert_file}")
    print(f"  Microphone: {'ENABLED' if cfg.audio else 'disabled (--no-audio)'}")
    print()

    if HAS_QRCODE:
        url = f"{scheme}://{ips[0] if ips else 'localhost'}:{cfg.port}/"
        if cfg.qr_pin:
            url += f"?pin={pin}"
        print("  ┌─ Scan the QR code with the phone camera ──┐")
        print(
            "  │                                          │"
            if not cfg.qr_pin
            else "  │ (PIN embedded - auto-connect)            │"
        )
        print("  └──────────────────────────────────────────┘")
        print()
        for line in qr_ascii(url).split("\n"):
            print("      " + line)
        print()
        print(f"  URL: {url}")
    else:
        print("  1) On the phone (same Wi-Fi as the PC), open in the browser:")
        print()
        for ip in ips:
            extra = f"/?pin={pin}" if cfg.qr_pin else "/"
            print(f"        {scheme}://{ip}:{cfg.port}{extra}")
        print()
        print("  (Install 'qrcode' for a QR code: pip install qrcode[pil])")

    print()
    if not cfg.qr_pin:
        print(f"  2) Enter the PIN on the phone: {pin}   (shown in this terminal)")
    if cfg.https:
        print()
        print("  ⚠  CERTIFICATE WARNING: the browser will show 'Connection not")
        print("     secure'. This is NORMAL with a self-signed certificate:")
        print("       Chrome Android: 'Advanced' → 'Proceed to <ip> (unsafe)'")
        print("       Safari iOS:     'Show Details' → 'Visit this Website'")
    print()
    print("  3) The virtual webcam appears as 'PhoneCam' (Linux: /dev/video10).")
    print("     Select it in Discord, OBS, Zoom, etc.")
    print()
    print("-" * 64)
    print("Logs (Ctrl+C to stop):")
    print()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = Config(
        host=args.host,
        port=args.port,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate_kbps=args.bitrate,
        pin=args.pin,
        no_https=args.no_https,
        no_audio=args.no_audio,
        qr_pin=args.qr_pin,
        verbose=args.verbose,
    )
    setup_logging(cfg.verbose)

    pin = cfg.pin or generate_pin()

    cert_file = key_file = None
    if cfg.https:
        cert_file, key_file = ensure_ssl_cert(BASE_DIR / "certs")

    banner(cfg, pin, cert_file)

    app = create_app(cfg, pin)
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        ssl_certfile=str(cert_file) if cert_file else None,
        ssl_keyfile=str(key_file) if key_file else None,
        ws_max_size=MAX_SIGNAL_MSG_BYTES,
        log_level="debug" if cfg.verbose else "warning",
        access_log=cfg.verbose,
    )


if __name__ == "__main__":
    main()
