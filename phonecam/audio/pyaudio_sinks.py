"""PyAudio-based audio sinks for macOS (BlackHole) and Windows (VB-Cable)."""

from __future__ import annotations

import logging
import platform
import subprocess

from phonecam.audio import find_device_by_name
from phonecam.config import AUDIO_CHANNELS, AUDIO_CHUNK_MS, AUDIO_SAMPLE_RATE
from phonecam.dependencies import HAS_PYAUDIO, pyaudio

log = logging.getLogger(__name__)

IS_MACOS = platform.system() == "Darwin"


class PyAudioSink:
    """Writes PCM into a PyAudio output stream opened on a virtual device."""

    def __init__(self, pa: object, stream: object) -> None:
        self._pa = pa
        self._stream = stream

    def write(self, pcm: bytes) -> bool:
        try:
            self._stream.write(pcm)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("Error writing to PyAudio stream: %s", e)
            return False

    def close(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:  # noqa: BLE001
            log.debug("Error closing PyAudio stream", exc_info=True)
        try:
            self._pa.terminate()
        except Exception:  # noqa: BLE001
            log.debug("Error terminating PyAudio", exc_info=True)


def setup_pyaudio_sink() -> PyAudioSink | None:
    if not HAS_PYAUDIO:
        return None
    return _setup_macos() if IS_MACOS else _setup_windows()


def _setup_macos() -> PyAudioSink | None:
    """macOS: user installs BlackHole 2ch (https://existential.audio/blackhole/)."""
    log.info("macOS: looking for BlackHole (https://existential.audio/blackhole/)...")
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:  # noqa: BLE001
        log.warning("Error initializing PyAudio: %s", e)
        return None

    idx = find_device_by_name(pa, "BlackHole", output=True)
    if idx is None:
        log.warning(
            "BlackHole not found. Install it from https://existential.audio/blackhole/, "
            "then restart the audio server (sudo killall coreaudiod) and run PhoneCam again."
        )
        pa.terminate()
        return None
    return _open(pa, idx, "BlackHole")


def _setup_windows() -> PyAudioSink | None:
    """Windows: user installs VB-Cable (https://vb-audio.com/Cable/)."""
    log.info("Windows: looking for VB-Cable (https://vb-audio.com/Cable/)...")
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:  # noqa: BLE001
        log.warning("Error initializing PyAudio: %s", e)
        return None

    idx = find_device_by_name(pa, "CABLE Input", output=True)
    if idx is None:
        idx = find_device_by_name(pa, "VB-Audio", output=True)
    if idx is None:
        log.warning(
            "VB-Cable not found. Download it from https://vb-audio.com/Cable/, "
            "run the installer as admin, reboot and start PhoneCam again."
        )
        pa.terminate()
        return None
    return _open(pa, idx, "VB-Cable Input")


def _open(pa: object, device_idx: int, label: str) -> PyAudioSink | None:
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_SAMPLE_RATE,
            output=True,
            output_device_index=device_idx,
            frames_per_buffer=int(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS / 1000),
        )
    except Exception as e:  # noqa: BLE001
        log.error("Error opening %s: %s", label, e)
        pa.terminate()
        return None
    log.info(
        "Audio output opened on %s (device #%d) @ %d Hz mono 16-bit",
        label,
        device_idx,
        AUDIO_SAMPLE_RATE,
    )
    return PyAudioSink(pa, stream)


def record_virtual_mic_wav(duration_s: float) -> bytes | None:
    """Record from the Linux virtual mic for diagnostics (returns WAV bytes).

    Only meaningful on Linux (pw-record/parec); returns None elsewhere.
    """
    import shutil
    import tempfile

    pw_record = shutil.which("pw-record")
    parec = shutil.which("parec")
    if not (pw_record or parec):
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    if pw_record:
        cmd = [
            "pw-record",
            "--format=s16le",
            f"--rate={AUDIO_SAMPLE_RATE}",
            f"--channels={AUDIO_CHANNELS}",
            "-d=phonecam_mic",
            "--file-format=wav",
            f"--file={tmp.name}",
        ]
    else:
        cmd = [
            "parec",
            "--format=s16le",
            f"--rate={AUDIO_SAMPLE_RATE}",
            f"--channels={AUDIO_CHANNELS}",
            "-d=phonecam_mic",
            "--file-format=wav",
            f"--file={tmp.name}",
        ]

    import time

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(duration_s)
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    import os

    if os.path.exists(tmp.name):
        with open(tmp.name, "rb") as f:
            data = f.read()
        os.unlink(tmp.name)
        return data
    return None
