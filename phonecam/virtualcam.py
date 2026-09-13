"""Virtual webcam creation (pyvirtualcam) with fallbacks and health check."""

from __future__ import annotations

import logging
import platform
from typing import TYPE_CHECKING

from phonecam.config import CAM_MAX_CONSECUTIVE_ERRORS
from phonecam.dependencies import HAS_PYVC

if TYPE_CHECKING:
    import numpy as np
    import pyvirtualcam

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"
OS_NAME = "Windows" if IS_WINDOWS else "macOS" if IS_MACOS else "Linux"

log = logging.getLogger(__name__)


class VirtualCamera:
    """Thin wrapper around a pyvirtualcam.Camera with error throttling."""

    def __init__(self, cam: pyvirtualcam.Camera, native_fmt: str) -> None:
        self._cam = cam
        self.native_fmt = native_fmt  # "RGB" or "BGR"
        self._consecutive_errors = 0

    @property
    def device(self) -> str:
        return getattr(self._cam, "device", "?")

    def send(self, arr: np.ndarray) -> bool:
        """Send an RGB/BGR uint8 (h, w, 3) array. Returns False on failure."""
        try:
            self._cam.send(arr)
            self._consecutive_errors = 0
            return True
        except Exception as e:
            self._consecutive_errors += 1
            if (
                self._consecutive_errors == 1
                or self._consecutive_errors % CAM_MAX_CONSECUTIVE_ERRORS == 0
            ):
                log.error(
                    "Virtual camera send failed (%d consecutive): %s",
                    self._consecutive_errors,
                    e,
                )
                _log_setup_instructions()
            return False

    def close(self) -> None:
        try:
            self._cam.close()
        except Exception:
            log.debug("Error closing virtual camera", exc_info=True)


def init_virtual_cam(width: int, height: int, fps: int) -> VirtualCamera | None:
    """Create the virtual webcam, trying several pyvirtualcam signatures.

    Returns None (debug mode) when pyvirtualcam is missing or the backend
    device is unavailable - the server still runs and decodes frames.
    """
    if not HAS_PYVC:
        log.warning("pyvirtualcam absent - frames will be discarded (debug only).")
        return None

    import inspect

    import pyvirtualcam

    sig = inspect.signature(pyvirtualcam.Camera)
    params = set(sig.parameters.keys())
    kwargs: dict = {"width": width, "height": height, "fps": fps}

    if "fmt" in params:
        kwargs["fmt"] = pyvirtualcam.PixelFormat.BGR
        native_fmt = "BGR"
    elif "fourcc" in params:
        kwargs["fourcc"] = 0x32424752  # 'BGR2'
        native_fmt = "BGR"
    else:
        native_fmt = "RGB"

    if "delay" in params:
        kwargs["delay"] = 0

    try:
        cam = pyvirtualcam.Camera(**kwargs)
        return _created(cam, native_fmt, width, height, fps, sorted(kwargs.keys()))
    except TypeError as e:
        log.warning("pyvirtualcam signature incompatible (%s); trying fallbacks...", e)
    except RuntimeError as e:
        _log_fatal(e)
        return None

    fallbacks: list[tuple[dict, str]] = [
        (
            {"width": width, "height": height, "fps": fps, "fmt": pyvirtualcam.PixelFormat.BGR},
            "BGR",
        ),
        (
            {"width": width, "height": height, "fps": fps, "fmt": pyvirtualcam.PixelFormat.RGB},
            "RGB",
        ),
        (
            {
                "width": width,
                "height": height,
                "fps": fps,
                "fmt": pyvirtualcam.PixelFormat.BGR,
                "delay": 0,
            },
            "BGR",
        ),
        ({"width": width, "height": height, "fps": fps, "fourcc": 0x32424752}, "BGR"),
    ]
    last_err: Exception | None = None
    for fb_kwargs, fmt in fallbacks:
        try:
            cam = pyvirtualcam.Camera(**fb_kwargs)
            return _created(cam, fmt, width, height, fps, sorted(fb_kwargs.keys()))
        except TypeError:
            continue
        except RuntimeError as e:
            _log_fatal(e)
            return None
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    log.error("Could not create virtual webcam. Cause: %s", last_err)
    _log_setup_instructions()
    return None


def _created(
    cam: pyvirtualcam.Camera,
    native_fmt: str,
    width: int,
    height: int,
    fps: int,
    kwarg_names: list[str],
) -> VirtualCamera:
    log.info(
        "Virtual webcam created at %s (%dx%d @ %dfps, OS=%s, kwargs=%s, fmt=%s)",
        cam.device,
        width,
        height,
        fps,
        OS_NAME,
        kwarg_names,
        native_fmt,
    )
    return VirtualCamera(cam, native_fmt)


def _log_fatal(err: Exception) -> None:
    log.error("Could not create virtual webcam. Cause: %s", err)
    _log_setup_instructions()


def _log_setup_instructions() -> None:
    lines = ["Virtual webcam setup instructions:", ""]
    if IS_LINUX:
        lines += [
            "  Linux - load the v4l2loopback module:",
            "",
            "    sudo modprobe v4l2loopback exclusive_caps=1 \\",
            '         video_nr=10 card_label="PhoneCam"',
            "",
            "  Module installation:",
            "    Arch Linux:    sudo pacman -S v4l2loopback-dkms",
            "    Ubuntu/Debian: sudo apt install v4l2loopback-dkms",
            "    Fedora:        sudo dnf install v4l2loopback",
        ]
    elif IS_WINDOWS:
        lines += [
            "  Windows - install OBS Studio (free):",
            "    https://obsproject.com/download",
            "",
            "  Open OBS once and start 'Virtual Camera' to register the DLL,",
            "  then close OBS (the DLL stays registered).",
            "  Alternative without OBS: 'Unity Capture' or 'OBS-VirtualCam'.",
        ]
    elif IS_MACOS:
        lines += [
            "  macOS - install OBS Studio (free):",
            "    https://obsproject.com/download",
            "",
            "  Open OBS once and start 'Virtual Camera' to register the plugin.",
            "  On Sonoma+ grant OBS camera permission in",
            "  System Settings -> Privacy & Security -> Camera.",
        ]
    else:
        lines.append(f"  Unrecognized OS: {OS_NAME}")
    for line in lines:
        log.error(line)
