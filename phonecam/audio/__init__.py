"""Cross-platform virtual microphone sinks.

The phone's Opus audio track is decoded by aiortc into PCM s16le mono
48 kHz. An ``AudioSink`` writes that PCM into a virtual microphone that
Discord/OBS/Zoom can select as an input device:

- Linux:   null sink + virtual source via pactl, fed by pw-cat/paplay stdin.
- macOS:   BlackHole 2ch via PyAudio.
- Windows: VB-Cable via PyAudio.
"""

from __future__ import annotations

import logging
import platform
from typing import Protocol

from phonecam.dependencies import HAS_PYAUDIO

log = logging.getLogger(__name__)

IS_LINUX = platform.system() == "Linux"


class AudioSink(Protocol):
    """Where decoded phone audio gets written."""

    def write(self, pcm: bytes) -> bool:
        """Write a PCM s16le mono 48 kHz chunk. False = unrecoverable."""
        ...

    def close(self) -> None: ...


def setup_virtual_mic() -> AudioSink | None:
    """Create the platform-appropriate sink, or None (audio disabled)."""
    if not HAS_PYAUDIO and not IS_LINUX:
        log.warning("pyaudio not installed - phone audio disabled.")
        log.warning("  pip install pyaudio (plus portaudio dev packages)")
        return None

    if IS_LINUX:
        from phonecam.audio.linux import setup_linux_sink

        return setup_linux_sink()
    from phonecam.audio.pyaudio_sinks import setup_pyaudio_sink

    return setup_pyaudio_sink()


def find_device_by_name(pa: object, name_pattern: str, output: bool = True) -> int | None:
    """Find a PyAudio device index whose name contains the pattern."""
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = str(info.get("name", "")).lower()
            if name_pattern.lower() in name:
                if output and info.get("maxOutputChannels", 0) > 0:
                    return i
                if not output and info.get("maxInputChannels", 0) > 0:
                    return i
    except Exception as e:  # noqa: BLE001
        log.warning("Error searching audio device: %s", e)
    return None
