"""Optional dependency detection with friendly diagnostics.

Every optional integration reports availability through a module-level
flag so the server can degrade gracefully instead of crashing.
"""

from __future__ import annotations

import sys


def _warn(msg: str) -> None:
    print(f"[WARNING] {msg}", file=sys.stderr)


# --- numpy / PIL / FastAPI are hard requirements ------------------------
try:
    import numpy  # noqa: F401  (availability check)
except ImportError:  # pragma: no cover
    _warn("numpy not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from PIL import Image  # noqa: F401
except ImportError:  # pragma: no cover
    _warn("Pillow not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    import uvicorn  # noqa: F401
    from fastapi import FastAPI  # noqa: F401
except ImportError:  # pragma: no cover
    _warn("fastapi/uvicorn not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

# --- pyvirtualcam (virtual webcam) --------------------------------------
try:
    import pyvirtualcam

    HAS_PYVC = True
except ImportError:
    pyvirtualcam = None  # type: ignore[assignment]
    HAS_PYVC = False
    _warn("pyvirtualcam not installed - virtual webcam disabled (debug mode).")

# --- qrcode -------------------------------------------------------------
try:
    import qrcode  # noqa: F401

    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    _warn("qrcode not installed - QR code disabled (run: pip install qrcode[pil]).")

# --- pyaudio (macOS/Windows audio sinks) --------------------------------
try:
    if sys.platform == "linux":
        # Silence the ALSA warnings PyAudio prints while enumerating devices
        # ("Unknown PCM cards.pcm.rear" etc.) on systems without full ALSA.
        import ctypes

        try:
            _asound = ctypes.cdll.LoadLibrary("libasound.so.2")
            _handler = ctypes.CFUNCTYPE(
                None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
            )
            _alsa_quiet = _handler(lambda *_: None)  # keep a reference: noqa: F841
            _asound.snd_lib_error_set_handler(_alsa_quiet)
        except Exception:
            pass

    import pyaudio

    HAS_PYAUDIO = True
except Exception:
    pyaudio = None  # type: ignore[assignment]
    HAS_PYAUDIO = False
    _warn("pyaudio failed to import - phone audio disabled on macOS/Windows.")

# --- aiortc (WebRTC) ----------------------------------------------------
try:
    import aiortc  # noqa: F401

    HAS_AIORTC = True
except ImportError:
    HAS_AIORTC = False
    _warn("aiortc not installed - WebRTC transport unavailable (pip install aiortc).")
