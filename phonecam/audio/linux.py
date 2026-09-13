"""Linux audio sink: PipeWire/PulseAudio null sink + pw-cat/paplay stdin.

Architecture:
    null sink "phonecam_mic_sink" (OUTPUT)  <- pw-cat plays PCM here
                | (internal monitor)
                v
    virtual source "PhoneCam Mic" (INPUT)  <- Discord/OBS select this

The virtual source solves Discord not listing "Monitor of ..." as input.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time

from phonecam.audio import AudioSink, find_device_by_name  # noqa: F401 (re-export helper)
from phonecam.config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE

log = logging.getLogger(__name__)

SINK_NAME = "phonecam_mic_sink"
SOURCE_NAME = "phonecam_mic"
SOURCE_DESC = "PhoneCam Mic"


class CliAudioSink(AudioSink):
    """Feeds PCM to a pw-cat/paplay subprocess through stdin.

    If the subprocess dies, one restart is attempted transparently.
    """

    def __init__(self, tool: str, proc: subprocess.Popen, extra_procs: list | None = None) -> None:
        self._tool = tool
        self._proc = proc
        self._extra_procs = extra_procs or []  # e.g. pw-loopback (source)
        self._sink_name = SINK_NAME

    def write(self, pcm: bytes) -> bool:
        if self._proc.poll() is not None:
            log.warning("%s died (exit code %s) - restarting", self._tool, self._proc.returncode)
            if not self._restart():
                return False
        try:
            self._proc.stdin.write(pcm)  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            log.warning("Error writing to %s stdin: %s - restarting", self._tool, e)
            if self._restart():
                try:
                    self._proc.stdin.write(pcm)  # type: ignore[union-attr]
                    self._proc.stdin.flush()  # type: ignore[union-attr]
                    return True
                except Exception as e2:  # noqa: BLE001
                    log.error("Write failed even after restart: %s", e2)
            return False

    def close(self) -> None:
        for proc in [self._proc, *self._extra_procs]:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc = None  # type: ignore[assignment]

    def _restart(self) -> bool:
        """Replace the dead playback process with a fresh one."""
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                self._proc.wait(timeout=2)
        except Exception:
            pass
        proc = _start_playback_proc(self._sink_name)
        if proc is None:
            return False
        self._proc = proc
        return True


def setup_linux_sink() -> AudioSink | None:
    if not _has_pulseaudio():
        log.warning("No PulseAudio/PipeWire server detected - audio disabled.")
        return None

    log.info("Creating virtual microphone '%s' in PulseAudio/PipeWire...", SOURCE_DESC)
    _create_null_sink(SINK_NAME, "PhoneCam Mic Sink")
    extra_procs = _create_virtual_source(SOURCE_NAME, SOURCE_DESC, f"{SINK_NAME}.monitor")

    proc = _start_playback_proc(SINK_NAME)
    if proc is None:
        for p in extra_procs:
            try:
                p.terminate()
            except Exception:
                pass
        return None

    log.info("Virtual microphone ready: '%s' @ %d Hz mono 16-bit", SOURCE_DESC, AUDIO_SAMPLE_RATE)
    log.info("Select '%s' as INPUT device in Discord/OBS/Zoom.", SOURCE_DESC)
    return CliAudioSink("pw-cat/paplay", proc, extra_procs)


def _has_pulseaudio() -> bool:
    try:
        r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:  # noqa: BLE001
        return False


def _create_null_sink(sink_name: str, sink_desc: str) -> None:
    """Create the null sink idempotently (unload + reload to fix format)."""
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "modules"], capture_output=True, text=True, timeout=3
        )
        for line in r.stdout.split("\n"):
            if "module-null-sink" in line and f"sink_name={sink_name}" in line:
                module_id = line.split()[0]
                log.debug("Unloading old module #%s to recreate with correct format", module_id)
                subprocess.run(
                    ["pactl", "unload-module", module_id], capture_output=True, timeout=3
                )
                break
    except Exception:  # noqa: BLE001
        pass

    cmd = [
        "pactl",
        "load-module",
        "module-null-sink",
        f"sink_name={sink_name}",
        f"sink_properties=device.description='{sink_desc}'",
        "channels=1",
        "rate=48000",
        "channel_map=mono",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            log.warning("pactl failed: %s", r.stderr.strip() or r.stdout.strip())
        else:
            log.info("Null sink created (module id %s)", r.stdout.strip())
    except Exception as e:  # noqa: BLE001
        log.warning("Error creating null sink: %s", e)


def _create_virtual_source(source_name: str, source_desc: str, master: str) -> list:
    """Create the virtual INPUT device. Returns procs to keep alive."""
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "modules"], capture_output=True, text=True, timeout=3
        )
        for line in r.stdout.split("\n"):
            if "module-virtual-source" in line and f"source_name={source_name}" in line:
                module_id = line.split()[0]
                log.debug("Unloading old virtual source #%s", module_id)
                subprocess.run(
                    ["pactl", "unload-module", module_id], capture_output=True, timeout=3
                )
                break
    except Exception:  # noqa: BLE001
        pass

    # media.class=Audio/Source makes PipeWire expose it as a microphone.
    cmd = [
        "pactl",
        "load-module",
        "module-virtual-source",
        f"source_name={source_name}",
        f"source_properties=device.description='{source_desc}',"
        f"device.icon_name='audio-input-microphone',"
        f"media.class='Audio/Source'",
        f"master={master}",
        "rate=48000",
        "channels=1",
        "channel_map=mono",
        "use_system_clock_for_timing=yes",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            log.warning("module-virtual-source failed: %s", (r.stderr or r.stdout).strip())
            log.info("Trying pw-loopback (PipeWire native)...")
            proc = _create_pw_loopback(source_name, source_desc, master)
            return [proc] if proc else []
        log.info("Virtual source created (module id %s)", r.stdout.strip())
        log.info("Select '%s' as INPUT device in Discord/OBS/Zoom", source_desc)
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("Error creating virtual source: %s", e)
        return []


def _create_pw_loopback(source_name: str, source_desc: str, master: str) -> subprocess.Popen | None:
    pw_loopback = shutil.which("pw-loopback")
    if not pw_loopback:
        return None

    playback_props = json.dumps(
        {
            "node.name": source_name,
            "node.description": source_desc,
            "media.class": "Audio/Source",
            "device.icon_name": "audio-input-microphone",
            "device.description": source_desc,
        }
    )
    capture_props = json.dumps(
        {
            "node.name": f"{source_name}_capture",
            "media.class": "Audio/Sink",
        }
    )
    cmd = [
        pw_loopback,
        "--capture",
        master,
        f"--playback-props={playback_props}",
        f"--capture-props={capture_props}",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(0.8)
        if proc.poll() is None:
            return proc
        err = proc.stderr.read().decode("utf-8", errors="replace").strip()  # type: ignore[union-attr]
        if err:
            log.warning("pw-loopback stderr: %s", err)
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("Exception starting pw-loopback: %s", e)
        return None


def _start_playback_proc(sink_name: str) -> subprocess.Popen | None:
    """Start pw-cat (preferred) or paplay reading raw PCM from stdin."""
    pwcat = shutil.which("pw-cat")
    paplay = shutil.which("paplay")

    if pwcat:
        cmd = [
            pwcat,
            "--playback",
            "--target",
            sink_name,
            "--format",
            "s16",
            "--rate",
            str(AUDIO_SAMPLE_RATE),
            "--channels",
            str(AUDIO_CHANNELS),
            "--raw",
            "--volume",
            "1.0",
        ]
        tool = "pw-cat"
    elif paplay:
        cmd = [
            paplay,
            "--raw",
            "--format=s16le",
            f"--rate={AUDIO_SAMPLE_RATE}",
            f"--channels={AUDIO_CHANNELS}",
            f"--device={sink_name}",
            "--stream-name=PhoneCam",
            "--client-name=PhoneCam",
            "--volume=65536",
        ]
        tool = "paplay"
    else:
        log.warning("Neither 'pw-cat' nor 'paplay' found in PATH.")
        log.warning("  Arch (PipeWire): sudo pacman -S pipewire")
        log.warning("  Ubuntu:          sudo apt install pulseaudio-utils")
        log.warning("  Fedora:          sudo dnf install pipewire pulseaudio-utils")
        return None

    log.info("Starting %s -> %s", tool, sink_name)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Could not start %s: %s", tool, e)
        return None

    _pipe_stderr(proc, tool)

    time.sleep(0.5)
    if proc.poll() is not None:
        err = ""
        try:
            err = proc.stderr.read().decode("utf-8", errors="replace").strip()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        log.warning(
            "%s exited immediately (code %s)%s", tool, proc.returncode, f": {err}" if err else ""
        )
        # pw-cat failing on some systems - retry once with paplay.
        if tool == "pw-cat" and paplay:
            log.info("Trying paplay as fallback...")
            return _start_playback_proc_paplay(sink_name, paplay)
        return None
    return proc


def _start_playback_proc_paplay(sink_name: str, paplay: str) -> subprocess.Popen | None:
    cmd = [
        paplay,
        "--raw",
        "--format=s16le",
        f"--rate={AUDIO_SAMPLE_RATE}",
        f"--channels={AUDIO_CHANNELS}",
        f"--device={sink_name}",
        "--stream-name=PhoneCam",
        "--client-name=PhoneCam",
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Could not start paplay: %s", e)
        return None
    _pipe_stderr(proc, "paplay")
    time.sleep(0.5)
    if proc.poll() is not None:
        log.warning("paplay exited immediately (code %s)", proc.returncode)
        return None
    return proc


def _pipe_stderr(proc: subprocess.Popen, tool: str) -> None:
    def _log_stderr() -> None:
        try:
            assert proc.stderr is not None
            for line in iter(proc.stderr.readline, b""):
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    log.debug("[%s] %s", tool, msg)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_log_stderr, daemon=True, name=f"{tool}-stderr").start()
