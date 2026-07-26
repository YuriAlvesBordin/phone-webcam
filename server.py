#!/usr/bin/env python3

import argparse
import asyncio
import datetime
import io
import ipaddress
import os
import platform
import secrets
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("[ERROR] numpy not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("[ERROR] Pillow not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import uvicorn
    from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
except ImportError:
    print("[ERROR] fastapi/uvicorn not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import pyvirtualcam
    HAS_PYVC = True
except ImportError:
    HAS_PYVC = False
    print("[WARNING] pyvirtualcam not installed — virtual webcam disabled (debug mode).", file=sys.stderr)

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    print("[WARNING] qrcode not installed — QR Code disabled (run: pip install qrcode[pil]).", file=sys.stderr)

try:
    # Silence ALSA warnings when enumerating devices
    # (ALSA prints "Unknown PCM cards.pcm.rear" etc for each missing device)
    import ctypes
    try:
        _asound = ctypes.cdll.LoadLibrary("libasound.so.2")
        _c_error_handler = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        # Keep global reference to prevent GC collection
        _alsa_silence_handler = _c_error_handler(lambda *_: None)
        _asound.snd_lib_error_set_handler(_alsa_silence_handler)
    except Exception:
        pass

    import pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False
except Exception as e:
    HAS_PYAUDIO = False
    print(f"[WARNING] pyaudio failed to initialize ({e}) — phone audio disabled.", file=sys.stderr)

# OS detection
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS   = platform.system() == "Darwin"
IS_LINUX   = platform.system() == "Linux"
OS_NAME    = "Windows" if IS_WINDOWS else "macOS" if IS_MACOS else "Linux"

# Global state
class State:
    def __init__(self):
        self.cam = None
        self.client: WebSocket | None = None
        self.client_lock = asyncio.Lock()
        self.frames_received = 0
        self.fps_counter = 0
        self.last_fps_time = time.time()
        self.current_fps = 0.0
        self.connected_since = 0.0
        self.client_addr = ""
        self.native_fmt = "BGR"
        # Audio
        self.audio_out = None
        self.audio_pa = None
        self.audio_enabled = False
        self.audio_packets = 0
        self._pw_loopback_proc = None


state = State()

BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = getattr(app.state, "cfg", None)
    if cfg and state.cam is None:
        state.cam = init_virtual_cam(cfg.width, cfg.height, cfg.fps)
    if cfg and getattr(cfg, "audio", False) and state.audio_out is None:
        pa_instance, audio_out = setup_virtual_mic()
        state.audio_pa = pa_instance
        state.audio_out = audio_out
        state.audio_enabled = audio_out is not None
    try:
        yield
    finally:
        if state.cam:
            try:
                state.cam.close()
            except Exception:
                pass
        teardown_audio()


app = FastAPI(title="PhoneCam", lifespan=lifespan)


# Utilities
def get_local_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip in ips:
                continue
            ips.append(ip)
    except Exception:
        pass

    # Fallback: create UDP socket to discover default outbound IP
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            ips.append("<your-ip-here>")

    return ips


def ensure_ssl_cert(cert_dir: Path) -> tuple[Path, Path]:
    cert_file = cert_dir / "phonecam.pem"
    key_file = cert_dir / "phonecam.key"

    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    cert_dir.mkdir(parents=True, exist_ok=True)

    # Collect local IPs for SAN (Subject Alternative Name)
    san_ips = [ipaddress.ip_address("127.0.0.1")]
    for ip in get_local_ips():
        try:
            san_ips.append(ipaddress.ip_address(ip))
        except ValueError:
            pass

    # Path 1: cryptography library (preferred)
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        print(">>> Generating self-signed SSL certificate (cryptography)...")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "PhoneCam"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PhoneCam Local"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
            .not_valid_after(
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName("PhoneCam")] + [x509.IPAddress(ip) for ip in san_ips]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return cert_file, key_file
    except ImportError:
        pass

    # Path 2: openssl CLI (fallback)
    print(">>> Generating self-signed SSL certificate (openssl)...")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_file),
            "-out", str(cert_file),
            "-days", "365", "-nodes",
            "-subj", "/CN=PhoneCam/O=PhoneCam Local",
        ],
        check=True,
        capture_output=True,
    )
    return cert_file, key_file


def init_virtual_cam(width: int, height: int, fps: int):
    if not HAS_PYVC:
        print("[WARNING] pyvirtualcam absent — frames will be discarded (debug only).")
        return None

    import inspect

    # Build kwargs via signature introspection
    sig = inspect.signature(pyvirtualcam.Camera)
    params = set(sig.parameters.keys())
    kwargs = {"width": width, "height": height, "fps": fps}

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

    # Try to create camera
    try:
        cam = pyvirtualcam.Camera(**kwargs)
        state.native_fmt = native_fmt
        print(f"[OK] Virtual webcam created at: {cam.device}")
        print(f"     Resolution: {width}x{height} @ {fps}fps")
        print(f"     OS: {OS_NAME} | pyvirtualcam {getattr(pyvirtualcam, '__version__', '?')}")
        print(f"     (kwargs: {sorted(kwargs.keys())}, fmt: {native_fmt})")
        return cam
    except TypeError as e:
        print(f"[WARNING] pyvirtualcam signature incompatible ({e}); trying fallbacks...")
        last_err = e
    except RuntimeError as e:
        print()
        print("[ERROR] Could not create virtual webcam.")
        print(f"       Cause: {e}")
        print()
        _print_webcam_setup_instructions()
        return None
    except Exception as e:
        last_err = e

    # Fallbacks (only if TypeError)
    fallbacks = [
        ({"width": width, "height": height, "fps": fps, "fmt": pyvirtualcam.PixelFormat.BGR}, "BGR"),
        ({"width": width, "height": height, "fps": fps, "fmt": pyvirtualcam.PixelFormat.RGB}, "RGB"),
        ({"width": width, "height": height, "fps": fps, "fmt": pyvirtualcam.PixelFormat.BGR, "delay": 0}, "BGR"),
        ({"width": width, "height": height, "fps": fps, "fourcc": 0x32424752}, "BGR"),
    ]

    for fb_kwargs, fmt in fallbacks:
        try:
            cam = pyvirtualcam.Camera(**fb_kwargs)
            state.native_fmt = fmt
            print(f"[OK] Virtual webcam created at: {cam.device}")
            print(f"     Resolution: {width}x{height} @ {fps}fps")
            print(f"     Backend: pyvirtualcam (fallback, kwargs: {sorted(fb_kwargs.keys())}, fmt: {fmt})")
            return cam
        except TypeError:
            continue
        except RuntimeError as e:
            print()
            print("[ERROR] Could not create virtual webcam.")
            print(f"       Cause: {e}")
            print()
            _print_webcam_setup_instructions()
            return None
        except Exception as e:
            last_err = e
            continue

    print()
    print("[ERROR] Could not create virtual webcam.")
    print(f"       Cause: {last_err}")
    print()
    _print_webcam_setup_instructions()
    return None


def _print_webcam_setup_instructions():
    print("Virtual webcam setup instructions:")
    print()
    if IS_LINUX:
        print("  Linux — load v4l2loopback module:")
        print()
        print("    sudo modprobe v4l2loopback exclusive_caps=1 \\")
        print('         video_nr=10 card_label="PhoneCam"')
        print()
        print("  Module installation:")
        print("    Arch Linux:    sudo pacman -S v4l2loopback-dkms")
        print("    Ubuntu/Debian: sudo apt install v4l2loopback-dkms")
        print("    Fedora:        sudo dnf install v4l2loopback")
    elif IS_WINDOWS:
        print("  Windows — install OBS Studio (free):")
        print("    https://obsproject.com/download")
        print()
        print("  After installing, OPEN OBS Studio once and start")
        print("  'Virtual Camera' (button 'Start Virtual Camera' in the")
        print("  controls panel). This registers the virtual camera DLL in Windows.")
        print()
        print("  Then close OBS — the DLL stays registered.")
        print()
        print("  Alternative without OBS: install 'Unity Capture' or 'OBS-VirtualCam'")
        print("  standalone (search GitHub).")
    elif IS_MACOS:
        print("  macOS — install OBS Studio (free):")
        print("    https://obsproject.com/download")
        print()
        print("  After installing, OPEN OBS Studio once and start")
        print("  'Virtual Camera'. This registers the virtual camera plugin.")
        print()
        print("  Note: on macOS Sonoma+ you may need to grant camera")
        print("  permission to OBS in System Settings → Privacy & Security → Camera.")
    else:
        print(f"  Unrecognized OS: {OS_NAME}")
    print()


# Virtual microphone (PulseAudio / PipeWire)
# Strategy: create a "null sink" in PulseAudio (or PipeWire via pactl)
# named "PhoneCam Mic" and monitor it. PyAudio opens output stream
# on that sink. Apps (Discord, OBS) select "PhoneCam Mic Monitor"
# as capture device to receive phone audio.
#
# PulseAudio:
#   pacmd load-module module-null-sink sink_name=phonecam_mic \
#       sink_properties=device.description="PhoneCam Mic"
#
# PipeWire (with pactl speaking pipewire-pulse protocol):
#   Same command works, but also auto-creates a monitor.

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_MS = 20


def setup_virtual_mic():
    """Create virtual microphone (cross-platform).

    Strategy per OS:
      - Linux:   create null sink in PulseAudio/PipeWire via `pactl`
      - macOS:   DOES NOT create automatically — user must install BlackHole
                 (https://existential.audio/blackhole/). PyAudio opens device.
      - Windows: DOES NOT create automatically — user must install VB-Cable
                 (https://vb-audio.com/Cable/). PyAudio opens device.

    Returns (pyaudio_instance, pyaudio_stream) or (None, None) if failed.
    """
    if not HAS_PYAUDIO:
        print("[WARNING] pyaudio not installed — phone audio disabled.")
        print("        Install with: pip install pyaudio")
        if IS_LINUX:
            print("        And on Arch: sudo pacman -S portaudio")
            print("        Ubuntu:     sudo apt install portaudio19-dev")
            print("        Fedora:     sudo dnf install portaudio-devel")
        elif IS_MACOS:
            print("        And on Mac:   brew install portaudio")
        elif IS_WINDOWS:
            print("        On Windows pyaudio usually comes with pre-compiled wheel.")
        return None, None

    if IS_LINUX:
        return _setup_mic_linux()
    elif IS_MACOS:
        return _setup_mic_macos()
    elif IS_WINDOWS:
        return _setup_mic_windows()
    else:
        print(f"[WARNING] OS not supported for audio: {OS_NAME}")
        return None, None


def _setup_mic_linux():
    """Linux: create null sink + virtual source for Discord to see as microphone.

    Architecture:
      null sink "phonecam_mic_sink" (OUTPUT) ← pw-cat plays PCM here
              ↓ (internal monitor)
      virtual source "PhoneCam Mic" (INPUT)  ← Discord/OBS selects as microphone

    This solves Discord not listing "Monitor of ..." as input device.
    With virtual source, Discord sees "PhoneCam Mic" directly as a microphone.
    """
    sink_name = "phonecam_mic_sink"
    source_name = "phonecam_mic"
    source_desc = "PhoneCam Mic"

    if not _has_pulseaudio():
        print("[WARNING] No PulseAudio/PipeWire server detected — audio disabled.")
        return None, None

    print(f">>> Creating virtual microphone '{source_desc}' in PulseAudio/PipeWire...")
    # 1) Create null sink (where pw-cat will play audio)
    _create_null_sink_retryable(sink_name, "PhoneCam Mic Sink")
    # 2) Create virtual source (what apps will select as microphone)
    _create_virtual_source_retryable(source_name, source_desc, f"{sink_name}.monitor")

    # Play audio into null sink via pw-cat/paplay
    return _setup_mic_linux_fallback_paplay(sink_name, source_desc)


def _setup_mic_linux_fallback_paplay(sink_name: str, sink_desc: str):
    """Use pw-cat (PipeWire native) or paplay (PulseAudio CLI) to play PCM.

    Priority:
      1. pw-cat (native PipeWire, more reliable on modern Arch/Fedora)
      2. paplay (PulseAudio compatibility, works on older systems)
    """
    import shutil
    import threading

    pwcat = shutil.which("pw-cat")
    paplay = shutil.which("paplay")

    if pwcat:
        # pw-cat --playback --target phonecam_mic --format s16 --rate 48000 --channels 1 --raw
        # --target tells pw-cat which sink to use (by name)
        cmd = [
            pwcat, "--playback",
            "--target", sink_name,
            "--format", "s16",
            "--rate", str(AUDIO_SAMPLE_RATE),
            "--channels", str(AUDIO_CHANNELS),
            "--raw",
            "--volume", "1.0",
        ]
        tool_name = "pw-cat"
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
        tool_name = "paplay"
    else:
        print("[!] Neither 'pw-cat' nor 'paplay' found in PATH.")
        print("    Install one of them:")
        print("      Arch (PipeWire): sudo pacman -S pipewire")
        print("      Ubuntu:          sudo apt install pulseaudio-utils")
        print("      Fedora:          sudo dnf install pipewire pulseaudio-utils")
        return None, None

    print(f">>> Starting {tool_name}...")
    print(f"    command: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        print(f"[!] Could not start {tool_name}: {e}")
        return None, None

    # Thread to read stderr and log (for diagnostics)
    def _log_stderr():
        try:
            for line in iter(proc.stderr.readline, b""):
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    print(f"[{tool_name}] {msg}")
        except Exception:
            pass
    threading.Thread(target=_log_stderr, daemon=True, name=f"{tool_name}-stderr").start()

    # Verify process is alive
    import time as _time
    _time.sleep(0.5)
    if proc.poll() is not None:
        try:
            err = proc.stderr.read().decode("utf-8", errors="replace").strip()
        except Exception:
            err = ""
        print(f"[!] {tool_name} exited immediately (exit code {proc.returncode})")
        if err:
            print(f"    stderr: {err}")
        # If pw-cat failed, try paplay as fallback
        if tool_name == "pw-cat" and paplay:
            print("    Trying paplay as fallback...")
            return _setup_mic_linux_fallback_paplay_paplay_only(sink_name, sink_desc, paplay)
        return None, None

    print(f"[OK] Virtual microphone created: '{sink_desc}' (via {tool_name})")
    print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
    print(f"     Use 'Monitor of {sink_desc}' as capture device in Discord/OBS/Zoom.")
    return (None, (tool_name, proc))


def _setup_mic_linux_fallback_paplay_paplay_only(sink_name: str, sink_desc: str, paplay: str):
    """Fallback: use only paplay (when pw-cat fails)."""
    import threading

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
    print(">>> Starting paplay (fallback)...")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except Exception as e:
        print(f"[!] Could not start paplay: {e}")
        return None, None

    def _log_stderr():
        try:
            for line in iter(proc.stderr.readline, b""):
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    print(f"[paplay] {msg}")
        except Exception:
            pass
    threading.Thread(target=_log_stderr, daemon=True, name="paplay-stderr").start()

    import time as _time
    _time.sleep(0.5)
    if proc.poll() is not None:
        try:
            err = proc.stderr.read().decode("utf-8", errors="replace").strip()
        except Exception:
            err = ""
        print(f"[!] paplay exited immediately (exit code {proc.returncode})")
        if err:
            print(f"    stderr: {err}")
        return None, None

    print(f"[OK] Virtual microphone created: '{sink_desc}' (via paplay)")
    print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
    print(f"     Use 'Monitor of {sink_desc}' as capture device in Discord/OBS/Zoom.")
    return (None, ("paplay", proc))


def _setup_mic_macos():
    """macOS: user installs BlackHole 2ch (https://existential.audio/blackhole/).

    PyAudio then opens BlackHole as output device.
    Apps select "BlackHole 2ch" as microphone.
    """
    print(">>> macOS: looking for BlackHole (install from https://existential.audio/blackhole/)...")
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:
        print(f"[!] Error initializing PyAudio: {e}")
        return None, None

    # Find BlackHole device
    blackhole_idx = _find_device_by_name(pa, "BlackHole", output=True)
    if blackhole_idx is None:
        print("[!] BlackHole not found. Install:")
        print("    1) Download from: https://existential.audio/blackhole/")
        print("    2) Install the .pkg")
        print("    3) Restart audio server: sudo killall coreaudiod")
        print("    4) Run again: ./run.py --audio")
        print()
        print("    Alternative: Loopback (https://rogueamoeba.com/loopback/) — paid.")
        return pa, None

    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_SAMPLE_RATE,
            output=True,
            output_device_index=blackhole_idx,
            frames_per_buffer=int(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS / 1000),
        )
        print(f"[OK] Audio output opened on BlackHole (device #{blackhole_idx})")
        print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
        print("     Use 'BlackHole 2ch' as capture device in Discord/OBS/Zoom.")
        return pa, stream
    except Exception as e:
        print(f"[!] Error opening BlackHole: {e}")
        return pa, None


def _setup_mic_windows():
    """Windows: user installs VB-Cable (https://vb-audio.com/Cable/).

    PyAudio opens "CABLE Input" as output. Apps select "CABLE Output"
    as microphone.
    """
    print(">>> Windows: looking for VB-Cable (install from https://vb-audio.com/Cable/)...")
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:
        print(f"[!] Error initializing PyAudio: {e}")
        return None, None

    # Find "CABLE Input" (VB-Audio Virtual Cable)
    cable_idx = _find_device_by_name(pa, "CABLE Input", output=True)
    if cable_idx is None:
        # Try also "VB-Audio"
        cable_idx = _find_device_by_name(pa, "VB-Audio", output=True)
    if cable_idx is None:
        print("[!] VB-Cable not found. Install:")
        print("    1) Download from: https://vb-audio.com/Cable/")
        print("    2) Extract and run VBCABLE_Setup_x64.exe as admin")
        print("    3) Reboot PC")
        print("    4) Run again: python run.py --audio")
        print()
        print("    Alternative: VoiceMeeter (https://vb-audio.com/Voicemeeter/) — more features.")
        return pa, None

    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_SAMPLE_RATE,
            output=True,
            output_device_index=cable_idx,
            frames_per_buffer=int(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS / 1000),
        )
        print(f"[OK] Audio output opened on VB-Cable Input (device #{cable_idx})")
        print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
        print("     Use 'CABLE Output' as capture device in Discord/OBS/Zoom.")
        return pa, stream
    except Exception as e:
        print(f"[!] Error opening VB-Cable: {e}")
        return pa, None


def _find_device_by_name(pa, name_pattern: str, output: bool = True) -> int | None:
    """Find device index whose name contains name_pattern (case-insensitive)."""
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info.get("name", "").lower()
            if name_pattern.lower() in name:
                if output and info.get("maxOutputChannels", 0) > 0:
                    return i
                if not output and info.get("maxInputChannels", 0) > 0:
                    return i
    except Exception as e:
        print(f"[!] Error searching device: {e}")
    return None


def _has_pulseaudio() -> bool:
    """Check if pactl is available and can talk to server."""
    try:
        r = subprocess.run(
            ["pactl", "info"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _create_null_sink_retryable(sink_name: str, sink_desc: str):
    """Create null sink idempotently.

    If sink with that name already exists, unload and recreate to ensure
    correct format (channels/rate).
    """
    # 1) Check if exists and unload if so (to recreate with correct format)
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "modules"],
            capture_output=True, text=True, timeout=3,
        )
        # Find module-null-sink with our sink_name and unload
        for line in r.stdout.split("\n"):
            if "module-null-sink" in line and f"sink_name={sink_name}" in line:
                module_id = line.split()[0]
                print(f"    (unloading old module #{module_id} to recreate with correct format)")
                subprocess.run(["pactl", "unload-module", module_id],
                              capture_output=True, timeout=3)
                break
    except Exception:
        pass

    # 2) Create via module-null-sink with explicit format
    cmd = [
        "pactl", "load-module",
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
            print(f"[!] pactl failed: {r.stderr.strip() or r.stdout.strip()}")
        else:
            print(f"    [OK] sink created (module id: {r.stdout.strip()})")
    except Exception as e:
        print(f"[!] Error creating null sink: {e}")


def _create_virtual_source_retryable(source_name: str, source_desc: str, master: str):
    """Create virtual source (INPUT device) that reads from master source.

    Tries two approaches:
    1. module-virtual-source (classic PulseAudio) — works on most systems
    2. pw-loopback (PipeWire native) — modern alternative

    Args:
        source_name: internal name (e.g., "phonecam_mic")
        source_desc: friendly name (e.g., "PhoneCam Mic")
        master: master source to read from (e.g., "phonecam_mic_sink.monitor")
    """
    # 1) Check if exists and unload
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "modules"],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.split("\n"):
            if "module-virtual-source" in line and f"source_name={source_name}" in line:
                module_id = line.split()[0]
                print(f"    (unloading old virtual source #{module_id})")
                subprocess.run(["pactl", "unload-module", module_id],
                              capture_output=True, timeout=3)
                break
    except Exception:
        pass

    # 2) Create via module-virtual-source
    # Important props for PipeWire to recognize as microphone:
    #   - device.description: friendly name
    #   - media.class: Audio/Source (required in PipeWire)
    #   - device.icon_name: shows microphone icon
    cmd = [
        "pactl", "load-module",
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
            print(f"[!] module-virtual-source failed: {r.stderr.strip() or r.stdout.strip()}")
            print("    Trying pw-loopback (PipeWire native)...")
            if _create_pw_loopback(source_name, source_desc, master):
                print("    [OK] pw-loopback created")
            else:
                print("    [!] pw-loopback also failed")
                print(f"    Alternative: use 'Monitor of {master.split('.')[0]}' as microphone")
        else:
            print(f"    [OK] virtual source created (module id: {r.stdout.strip()})")
            print(f"    Select '{source_desc}' as INPUT device in Discord/OBS/Zoom")
    except Exception as e:
        print(f"[!] Error creating virtual source: {e}")


def _create_pw_loopback(source_name: str, source_desc: str, master: str) -> bool:
    """Create virtual source via pw-loopback (PipeWire native).

    pw-loopback creates a node that captures from `master` and plays back
    to a new virtual node. To make that node appear as INPUT device
    (microphone) in Discord, we configure media.class=Audio/Source.
    """
    import json as _json
    import shutil

    pw_loopback = shutil.which("pw-loopback")
    if not pw_loopback:
        return False

    # Props for playback node (becomes the "virtual microphone")
    playback_props = _json.dumps({
        "node.name": source_name,
        "node.description": source_desc,
        "media.class": "Audio/Source",
        "device.icon_name": "audio-input-microphone",
        "device.description": source_desc,
    })
    # Props for capture node (hidden)
    capture_props = _json.dumps({
        "node.name": f"{source_name}_capture",
        "media.class": "Audio/Sink",
    })

    cmd = [
        pw_loopback,
        "--capture", master,
        f"--playback-props={playback_props}",
        f"--capture-props={capture_props}",
    ]

    print(f"    pw-loopback command: {' '.join(cmd[:4])} ...")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        import time as _t
        _t.sleep(0.8)
        if proc.poll() is None:
            # Save proc for cleanup on shutdown
            state._pw_loopback_proc = proc
            return True
        else:
            try:
                err = proc.stderr.read().decode("utf-8", errors="replace").strip()
                if err:
                    print(f"    pw-loopback stderr: {err}")
            except Exception:
                pass
            return False
    except Exception as e:
        print(f"    [!] Exception starting pw-loopback: {e}")
        return False


def _find_sink_device(pa, sink_desc: str) -> int | None:
    """Find PyAudio device index of monitor of sink 'PhoneCam Mic'.

    PyAudio lists each sink + its monitor as separate devices.
    We look for OUTPUT device called 'PhoneCam Mic' — writing to it
    sends sound to null sink, and corresponding monitor is what apps
    select as "microphone".
    """
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info.get("name", "")
            desc = info.get("name", "")  # PyAudio brings name, not description
            # Search by both sink_name and description
            if "phonecam" in name.lower() or "phonecam" in desc.lower():
                # Verify it's output (maxOutputChannels > 0)
                if info.get("maxOutputChannels", 0) > 0:
                    return i
    except Exception as e:
        print(f"[!] Error finding sink: {e}")
    return None


def _is_cli_audio(state_obj) -> bool:
    """Check if audio_out is a CLI subprocess (pw-cat or paplay)."""
    return (
        isinstance(state_obj, tuple)
        and len(state_obj) == 2
        and state_obj[0] in ("pw-cat", "paplay")
    )


def teardown_audio():
    """Close stream and PyAudio instance. Does not unload module (other
    apps may be using the monitor)."""
    # Kill pw-loopback if exists
    if state._pw_loopback_proc is not None:
        try:
            state._pw_loopback_proc.terminate()
            state._pw_loopback_proc.wait(timeout=2)
        except Exception:
            try:
                state._pw_loopback_proc.kill()
            except Exception:
                pass
        state._pw_loopback_proc = None

    if state.audio_out is None:
        return

    # Special case: CLI subprocess (pw-cat or paplay)
    if _is_cli_audio(state.audio_out):
        _, proc = state.audio_out
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
        state.audio_out = None
        return

    # Normal path: PyAudio
    if state.audio_out:
        try:
            state.audio_out.stop_stream()
            state.audio_out.close()
        except Exception:
            pass
        state.audio_out = None
    if state.audio_pa:
        try:
            state.audio_pa.terminate()
        except Exception:
            pass
        state.audio_pa = None


def write_audio_chunk(data: bytes) -> bool:
    """Write PCM chunk to virtual audio device.

    Returns True if OK, False if error (should close connection).
    Handles both paths: PyAudio stream and CLI subprocess (pw-cat/paplay).
    """
    if state.audio_out is None:
        return False

    # Special case: CLI subprocess (pw-cat or paplay)
    if _is_cli_audio(state.audio_out):
        tool_name, proc = state.audio_out
        try:
            if proc.poll() is not None:
                print(f"[!] {tool_name} died (exit code {proc.returncode})")
                # Try restart
                if _restart_paplay():
                    print(f"[OK] {tool_name} restarted — trying write again")
                    _, proc = state.audio_out
                    proc.stdin.write(data)
                    proc.stdin.flush()
                    return True
                return False
            proc.stdin.write(data)
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            print(f"[!] Error writing to {tool_name} stdin: {type(e).__name__}: {e}")
            # CLI probably died — try restart
            if _restart_paplay():
                print("[OK] restarted — trying write again")
                try:
                    _, proc = state.audio_out
                    proc.stdin.write(data)
                    proc.stdin.flush()
                    return True
                except Exception as e2:
                    print(f"[!] Failed even after restart: {e2}")
            return False
        except Exception as e:
            print(f"[!] Unexpected error writing audio: {type(e).__name__}: {e}")
            return False

    # Normal path: PyAudio
    try:
        state.audio_out.write(data)
        return True
    except Exception as e:
        print(f"[!] Error writing to PyAudio stream: {e}")
        return False


def _restart_paplay() -> bool:
    """Restart CLI subprocess (pw-cat or paplay) if it died."""
    if not IS_LINUX:
        return False
    try:
        # Kill old process if still running
        if _is_cli_audio(state.audio_out):
            _, old_proc = state.audio_out
            try:
                if old_proc.poll() is None:
                    old_proc.terminate()
                    old_proc.wait(timeout=2)
            except Exception:
                pass

        # Create new subprocess playing to null sink (not virtual source)
        # _setup_mic_linux_fallback_paplay returns (None, (tool_name, proc))
        pa_instance, audio_out = _setup_mic_linux_fallback_paplay("phonecam_mic_sink", "PhoneCam Mic")
        if audio_out is not None:
            state.audio_pa = pa_instance
            state.audio_out = audio_out
            return True
        return False
    except Exception as e:
        print(f"[!] Error restarting CLI audio: {e}")
        return False


def qr_ascii(url: str, compact: bool = True) -> str:
    """Generate QR Code as ASCII art for terminal display.

    Uses unicode blocks ' █' (space + block) to represent each module.
    Modern terminals render this as scannable QR Code.
    """
    if not HAS_QRCODE:
        return "[QR Code unavailable — install: pip install qrcode[pil]]"

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # Render as boolean matrix
    matrix = qr.get_matrix()
    h = len(matrix)
    w = len(matrix[0]) if h else 0

    # Compact 2 lines per text line using half-blocks (▀▄█)
    # This halves height and stays readable in small terminals
    lines = []
    for y in range(0, h, 2):
        row = []
        for x in range(w):
            top = matrix[y][x] if y < h else False
            bot = matrix[y + 1][x] if (y + 1) < h else False
            # Combine bits: top=UPPER, bot=LOWER
            # Use half-block chars:
            #   ▀ = only top black
            #   ▄ = only bottom black
            #   █ = both black
            #   space = both white
            if top and bot:
                row.append("█")
            elif top and not bot:
                row.append("▀")
            elif not top and bot:
                row.append("▄")
            else:
                row.append(" ")
        lines.append("".join(row))

    return "\n".join(lines)


def qr_png_bytes(url: str, size: int = 512) -> bytes:
    """Generate QR Code as PNG (returns bytes)."""
    if not HAS_QRCODE:
        raise RuntimeError("qrcode not installed")

    img = qrcode.make(url, box_size=10, border=2)
    img = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def banner(args, pin: str, cert_file: Path | None = None) -> None:
    ips = get_local_ips()
    scheme = "https" if not args.no_https else "http"
    print()
    print("=" * 64)
    print("  PhoneCam — Phone as Webcam on PC")
    print("=" * 64)
    print()
    print(f"  OS        : {OS_NAME}")
    print(f"  Resolution: {args.width}x{args.height} @ {args.fps}fps")
    print(f"  PIN       : {pin}")
    print(f"  Protocol  : {scheme.upper()}")
    if cert_file and not args.no_https:
        print(f"  Cert SSL  : {cert_file}")
    print(f"  Microphone: {'ENABLED' if args.audio else 'disabled (--no-audio)'}")
    print()

    # QR Code points to first URL with embedded PIN (auto-connect)
    base_host = ips[0] if ips else "localhost"
    qr_url = f"{scheme}://{base_host}:{args.port}/?pin={pin}"
    if HAS_QRCODE:
        print("  ┌─ Scan QR Code with phone camera ─┐")
        print("  │  (PIN already embedded — auto-connect)   │")
        print("  └────────────────────────────────────────┘")
        print()
        ascii_qr = qr_ascii(qr_url)
        # Indent QR Code to align in banner
        for line in ascii_qr.split("\n"):
            print("      " + line)
        print()
        print(f"  URL embedded in QR: {qr_url}")
        print()
    else:
        print("  1) On phone, connected to SAME Wi-Fi as PC,")
        print("     open one of the URLs below in browser (Chrome/Safari/Firefox):")
        print()
        for ip in ips:
            print(f"        {scheme}://{ip}:{args.port}/?pin={pin}")
        print()
        print("  (Install 'qrcode' for QR Code: pip install qrcode[pil])")
        print()

    if not args.no_https:
        print("  ⚠  CERTIFICATE WARNING: browser will show 'Connection not")
        print("     secure'. This is NORMAL — accept to continue:")
        print("       Chrome Android: 'Advanced' → 'Proceed to <ip> (unsafe)'")
        print("       Safari iOS:     'Show Details' → 'Visit this Website'")
        print()
    print(f"  2) Enter PIN: {pin}  (or scan QR above to skip this step)")
    print()
    print("  3) Virtual webcam will appear at /dev/video10 (or other /dev/videoN).")
    print("     Select \"PhoneCam\" in Discord, OBS, Zoom, etc.")
    print()
    print("-" * 64)
    print("Logs (Ctrl+C to stop):")
    print()


# Routes
@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/info")
async def info():
    cfg = app.state.cfg
    return {
        "width": cfg.width,
        "height": cfg.height,
        "fps": cfg.fps,
        "pin_required": True,
        "audio_enabled": state.audio_enabled,
        "audio_sample_rate": AUDIO_SAMPLE_RATE,
        "audio_channels": AUDIO_CHANNELS,
    }


@app.get("/qrcode.png")
async def qrcode_png():
    """PNG QR Code pointing to main PhoneCam URL (with embedded PIN)."""
    from fastapi import Response
    if not HAS_QRCODE:
        return JSONResponse({"error": "qrcode not installed"}, status_code=503)
    cfg = app.state.cfg
    ips = get_local_ips()
    scheme = "https" if not cfg.no_https else "http"
    pin = app.state.pin
    url = f"{scheme}://{ips[0] if ips else 'localhost'}:{cfg.port}/?pin={pin}"
    png = qr_png_bytes(url, size=512)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/qrcode")
async def qrcode_ascii_endpoint():
    """QR Code as ASCII art (for curl inspection). Includes PIN in URL."""
    from fastapi import Response
    if not HAS_QRCODE:
        return JSONResponse({"error": "qrcode not installed"}, status_code=503)
    cfg = app.state.cfg
    ips = get_local_ips()
    scheme = "https" if not cfg.no_https else "http"
    pin = app.state.pin
    url = f"{scheme}://{ips[0] if ips else 'localhost'}:{cfg.port}/?pin={pin}"
    ascii_qr = qr_ascii(url)
    return Response(
        content=f"PhoneCam QR Code\nURL: {url}\n\n{ascii_qr}\n",
        media_type="text/plain; charset=utf-8",
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, pin: str = Query(...)):
    """Receive binary JPEG frames from phone and write to virtual webcam."""
    cfg = app.state.cfg

    # Validate PIN before accepting
    if pin != app.state.pin:
        await ws.close(code=4003, reason="Invalid PIN")
        return

    # Only one active client at a time
    async with state.client_lock:
        if state.client is not None and state.client is not ws:
            try:
                await state.client.close(code=4009, reason="Another client connected")
            except Exception:
                pass
        state.client = ws
        state.client_addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
        state.connected_since = time.time()

    await ws.accept()
    print(f"[+] Client connected: {state.client_addr}")

    # Inactivity timeout: if no frame arrives in 15s, consider connection dead
    INACTIVITY_TIMEOUT = 15.0

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_bytes(), timeout=INACTIVITY_TIMEOUT)
            except asyncio.TimeoutError:
                # No frames for 15s — could be screen off or stuck connection
                print(f"\n[!] No frames for {INACTIVITY_TIMEOUT:.0f}s — closing connection")
                break

            try:
                img = Image.open(io.BytesIO(msg)).convert("RGB")
            except Exception as e:
                print(f"[!] Invalid frame ({e}), discarded")
                continue

            # Resize if needed
            if img.size != (cfg.width, cfg.height):
                img = img.resize((cfg.width, cfg.height), Image.LANCZOS)

            # Build numpy array in format expected by virtual camera
            # Detected during init_virtual_cam (RGB or BGR depending on backend)
            arr = np.asarray(img)
            if state.native_fmt == "BGR":
                arr = arr[:, :, ::-1]  # RGB -> BGR
            # if RGB, keep as-is

            if state.cam is not None:
                try:
                    state.cam.send(arr)
                except Exception as e:
                    print(f"[!] Error sending frame to v4l2: {e}")

            # FPS stats
            state.frames_received += 1
            state.fps_counter += 1
            now = time.time()
            if now - state.last_fps_time >= 1.0:
                state.current_fps = state.fps_counter / (now - state.last_fps_time)
                state.fps_counter = 0
                state.last_fps_time = now
                print(
                    f"\r[+] FPS: {state.current_fps:5.1f} | "
                    f"Total frames: {state.frames_received} | "
                    f"Client: {state.client_addr}    ",
                    end="",
                    flush=True,
                )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"\n[!] WebSocket error: {e}")
    finally:
        async with state.client_lock:
            if state.client is ws:
                state.client = None
                state.client_addr = ""
        print(f"\n[-] Client disconnected: {ws.client}")


@app.get("/audio-test")
async def audio_test():
    """Record 3 seconds from virtual mic (monitor) and return as WAV.

    Useful to test if phone audio is actually reaching the sink.
    Open https://<ip>:<port>/audio-test in PC browser to download WAV.
    """
    import shutil
    import tempfile

    from fastapi import Response

    # Record from virtual source "phonecam_mic" (not monitor of sink)
    # because that's what Discord sees as input device
    monitor_name = "phonecam_mic"
    duration_s = 3

    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav.close()

    pw_record = shutil.which("pw-record")
    parec = shutil.which("parec")

    if not (pw_record or parec):
        return JSONResponse(
            {"error": "No pw-record or parec found. Install pipewire or pulseaudio-utils."},
            status_code=503,
        )

    if pw_record:
        cmd = ["pw-record", "--format=s16le", f"--rate={AUDIO_SAMPLE_RATE}",
               f"--channels={AUDIO_CHANNELS}", f"-d={monitor_name}",
               "--file-format=wav", f"--file={tmp_wav.name}"]
    else:
        cmd = ["parec", "--format=s16le", f"--rate={AUDIO_SAMPLE_RATE}",
               f"--channels={AUDIO_CHANNELS}", f"-d={monitor_name}",
               "--file-format=wav", f"--file={tmp_wav.name}"]

    # Run for 3s and kill process
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    import time as _t
    _t.sleep(duration_s)
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if os.path.exists(tmp_wav.name):
        with open(tmp_wav.name, "rb") as f:
            wav_data = f.read()
        os.unlink(tmp_wav.name)
        return Response(
            content=wav_data,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=phonecam_audio_test.wav"},
        )
    else:
        return JSONResponse({"error": "Recording failed"}, status_code=500)


@app.websocket("/ws/audio")
async def ws_audio_endpoint(ws: WebSocket, pin: str = Query(...)):
    """Receive PCM 16-bit mono audio from phone and play to virtual mic.

    Format: binary chunks of PCM 16-bit signed little-endian, mono, 48kHz.
    Target latency: ~20ms per chunk.
    """
    if pin != app.state.pin:
        await ws.close(code=4003, reason="Invalid PIN")
        return

    if not state.audio_enabled or state.audio_out is None:
        await ws.close(code=4004, reason="Audio disabled on server (use --audio)")
        return

    await ws.accept()
    print(f"[+] Audio client connected: {ws.client.host if ws.client else '?'}")

    # Volume stats for diagnostics
    last_stats_time = time.time()
    stats_packets = 0
    max_sample_seen = 0

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_bytes(), timeout=60.0)
            except asyncio.TimeoutError:
                # No audio for 60s — consider connection dead
                break

            if not state.audio_enabled:
                continue

            # Convert Float32 -> Int16 PCM little-endian
            # (handled on phone side, we just forward binary)
            ok = write_audio_chunk(msg)
            if not ok:
                break

            state.audio_packets += 1
            stats_packets += 1
            now = time.time()
            if now - last_stats_time >= 5.0:
                print(f"    [audio] packets: {stats_packets} | max sample: {max_sample_seen}")
                stats_packets = 0
                max_sample_seen = 0
                last_stats_time = now
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"\n[!] Audio WebSocket error: {e}")
    finally:
        print(f"\n[-] Audio client disconnected: {ws.client}")


# Main entry point
def main():
    parser = argparse.ArgumentParser(description="PhoneCam — Phone as Webcam on PC")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="TCP port (default: 8765)")
    parser.add_argument("--width", type=int, default=1280, help="Width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="Height (default: 720)")
    parser.add_argument("--fps", type=int, default=30, help="Target FPS (default: 30)")
    parser.add_argument("--pin", type=str, default=None, help="Fixed PIN (default: random each run)")
    parser.add_argument("--no-https", action="store_true", help="Disable HTTPS (ONLY for localhost; phones require HTTPS)")
    parser.add_argument("--no-audio", action="store_true", help="Disable phone microphone (audio is ON by default)")
    args = parser.parse_args()

    # Generate random PIN if not provided
    pin = args.pin or f"{secrets.randbelow(900000) + 100000:06d}"

    # SSL cert
    cert_file = None
    if not args.no_https:
        cert_file, _ = ensure_ssl_cert(BASE_DIR / "certs")

    # Store config and PIN in app state
    app.state.cfg = args
    app.state.pin = pin

    # Print banner
    banner(args, pin, cert_file)

    # Run server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=cert_file if not args.no_https else None,
        ssl_keyfile=cert_file if not args.no_https else None,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
