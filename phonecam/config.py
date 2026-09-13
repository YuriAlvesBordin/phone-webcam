"""Runtime configuration and named constants."""

from __future__ import annotations

from dataclasses import dataclass

# --- Defaults -----------------------------------------------------------
DEFAULT_PORT = 8765
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 30
DEFAULT_BITRATE_KBPS = 4000
# Numeric PIN length (kept in sync with the frontend PIN input).
PIN_LENGTH = 6

# --- Protocol / security limits -----------------------------------------
# Close the session if no video frame arrives within this window.
INACTIVITY_TIMEOUT_S = 15.0
# A signaling client must authenticate within this window after connecting.
AUTH_TIMEOUT_S = 5.0
# Maximum accepted signaling message size (SDP + ICE candidates are small).
MAX_SIGNAL_MSG_BYTES = 64 * 1024
# Failed auth attempts per IP before temporary lockout.
AUTH_MAX_FAILURES = 5
AUTH_LOCKOUT_S = 60.0

# --- Audio --------------------------------------------------------------
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_MS = 20

# --- SSL ----------------------------------------------------------------
CERT_VALIDITY_DAYS = 365
CERT_KEY_SIZE = 2048

# --- Monitoring ---------------------------------------------------------
STATS_INTERVAL_S = 2.0
# Consecutive virtual-camera send errors before logging an actionable alert.
CAM_MAX_CONSECUTIVE_ERRORS = 60


@dataclass(slots=True)
class Config:
    """Everything the server needs to run, produced by the CLI parser."""

    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    pin: str | None = None  # fixed PIN, or None for a random one per run
    no_https: bool = False
    no_audio: bool = False
    # Embed the PIN in the QR code URL (convenient, but anyone who scans
    # it can connect; default is a PIN-less QR + manual entry).
    qr_pin: bool = False
    verbose: bool = False

    @property
    def https(self) -> bool:
        return not self.no_https

    @property
    def audio(self) -> bool:
        return not self.no_audio
