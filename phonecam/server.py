"""FastAPI application: static frontend + WebRTC signaling + diagnostics."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from phonecam.audio import setup_virtual_mic
from phonecam.config import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    AUTH_LOCKOUT_S,
    AUTH_MAX_FAILURES,
    Config,
)
from phonecam.dependencies import HAS_QRCODE
from phonecam.net import get_local_ips
from phonecam.qr import qr_ascii, qr_png_bytes
from phonecam.security import RateLimiter
from phonecam.signaling import SessionManager, signal_endpoint
from phonecam.virtualcam import init_virtual_cam

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
        "script-src 'self'; connect-src 'self' ws: wss:; media-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def connection_url(cfg: Config, pin: str) -> str:
    """URL embedded in the QR code (PIN only with the opt-in --qr-pin flag)."""
    ips = get_local_ips()
    scheme = "https" if cfg.https else "http"
    host = ips[0] if ips else "localhost"
    url = f"{scheme}://{host}:{cfg.port}/"
    if cfg.qr_pin:
        url += f"?pin={pin}"
    return url


def create_app(cfg: Config, pin: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.vcam is None:
            app.state.vcam = init_virtual_cam(cfg.width, cfg.height, cfg.fps)
        if cfg.audio and app.state.audio_sink is None:
            app.state.audio_sink = setup_virtual_mic()
            if app.state.audio_sink is None:
                log.warning("Virtual microphone unavailable - audio disabled for this run.")
        try:
            yield
        finally:
            if app.state.vcam is not None:
                app.state.vcam.close()
                app.state.vcam = None
            if app.state.audio_sink is not None:
                app.state.audio_sink.close()
                app.state.audio_sink = None
            current = app.state.manager.current
            if current is not None:
                await current.close()

    app = FastAPI(title="PhoneCam", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.pin = pin
    app.state.vcam = None
    app.state.audio_sink = None
    app.state.manager = SessionManager()
    app.state.rate_limiter = RateLimiter(AUTH_MAX_FAILURES, AUTH_LOCKOUT_S)

    app.add_api_websocket_route("/signal", signal_endpoint)

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/info")
    async def info() -> dict:
        return {
            "version": "2.0.0",
            "transport": "webrtc",
            "width": cfg.width,
            "height": cfg.height,
            "fps": cfg.fps,
            "bitrate_kbps": cfg.bitrate_kbps,
            "pin_required": True,
            "audio_enabled": app.state.audio_sink is not None,
            "audio_sample_rate": AUDIO_SAMPLE_RATE,
            "audio_channels": AUDIO_CHANNELS,
        }

    @app.get("/qrcode.png")
    async def qrcode_png() -> Response:
        if not HAS_QRCODE:
            return JSONResponse({"error": "qrcode not installed"}, status_code=503)
        png = qr_png_bytes(connection_url(cfg, pin), size=512)
        return Response(
            content=png,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/qrcode")
    async def qrcode_ascii_endpoint() -> Response:
        if not HAS_QRCODE:
            return JSONResponse({"error": "qrcode not installed"}, status_code=503)
        url = connection_url(cfg, pin)
        content = f"PhoneCam QR Code\nURL: {url}\n\n{qr_ascii(url)}\n"
        return Response(content=content, media_type="text/plain; charset=utf-8")

    @app.get("/audio-test")
    async def audio_test() -> Response:
        """Record 3s from the virtual mic and return a WAV (diagnostics)."""
        from phonecam.audio.pyaudio_sinks import record_virtual_mic_wav

        wav = await asyncio.to_thread(record_virtual_mic_wav, 3.0)
        if wav is None:
            return JSONResponse(
                {"error": "Recording unavailable (pw-record/parec not found)"},
                status_code=503,
            )
        return Response(
            content=wav,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=phonecam_audio_test.wav"},
        )

    app.mount("/css", StaticFiles(directory=STATIC_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")
    return app
