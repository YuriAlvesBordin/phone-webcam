#!/usr/bin/env python3
"""
PhoneCam - Use a câmera do celular como webcam virtual no Linux.

Arquitetura:
  - Servidor FastAPI + uvicorn escutando em 0.0.0.0:8765
  - Página HTML servida em GET / para o celular capturar a câmera via getUserMedia
  - WebSocket /ws?pin=XXXXXX recebe frames JPEG binários do celular
  - Frames decodificados com Pillow e escritos em /dev/videoN via pyvirtualcam (v4l2loopback)

Uso:
  python server.py [--host 0.0.0.0] [--port 8765] [--width 1280] [--height 720] [--fps 30]
"""

import argparse
import asyncio
import datetime
import io
import ipaddress
import math
import os
import platform
import secrets
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# --------------------------------------------------------------------------- #
# Detectação de OS
# --------------------------------------------------------------------------- #
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS   = platform.system() == "Darwin"
IS_LINUX   = platform.system() == "Linux"
OS_NAME    = "Windows" if IS_WINDOWS else "macOS" if IS_MACOS else "Linux"

try:
    import numpy as np
except ImportError:
    print("[ERRO] numpy não instalado. Rode: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("[ERRO] Pillow não instalado. Rode: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import uvicorn
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
    from fastapi.responses import FileResponse, JSONResponse
except ImportError:
    print("[ERRO] fastapi/uvicorn não instalados. Rode: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    import pyvirtualcam
    HAS_PYVC = True
except ImportError:
    HAS_PYVC = False
    print("[AVISO] pyvirtualcam não instalado — webcam virtual desativada (modo debug).",
          file=sys.stderr)

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    print("[AVISO] qrcode não instalado — QR Code desativado (rode: pip install qrcode[pil]).",
          file=sys.stderr)

try:
    # Silencia spam de warnings do ALSA quando enumerando dispositivos
    # (ALSA imprime "Unknown PCM cards.pcm.rear" etc para cada dispositivo faltante)
    import ctypes
    try:
        _asound = ctypes.cdll.LoadLibrary("libasound.so.2")
        _c_error_handler = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int,
                                            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
        # Mantém referência global para não ser coletado pelo GC
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
    print(f"[AVISO] pyaudio falhou ao inicializar ({e}) — áudio do celular desativado.",
          file=sys.stderr)


# --------------------------------------------------------------------------- #
# Estado global
# --------------------------------------------------------------------------- #
class State:
    def __init__(self):
        self.cam = None                  # pyvirtualcam.Camera (ou None)
        self.client: WebSocket | None = None
        self.client_lock = asyncio.Lock()
        self.frames_received = 0
        self.fps_counter = 0
        self.last_fps_time = time.time()
        self.current_fps = 0.0
        self.connected_since = 0.0
        self.client_addr = ""
        self.native_fmt = "BGR"          # "BGR" ou "RGB", definido em init_virtual_cam
        # Áudio
        self.audio_out = None            # pyaudio.Stream (output) ou tupla CLI
        self.audio_pa = None             # instância PyAudio
        self.audio_enabled = False
        self.audio_packets = 0
        self._pw_loopback_proc = None    # subprocess do pw-loopback (PipeWire)


state = State()

BASE_DIR = Path(__file__).parent.resolve()
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria webcam virtual + microfone virtual na inicialização e fecha no shutdown."""
    cfg = getattr(app.state, "cfg", None)
    if cfg and state.cam is None:
        state.cam = init_virtual_cam(cfg.width, cfg.height, cfg.fps)
    if cfg and getattr(cfg, "audio", False) and state.audio_out is None:
        # setup_virtual_mic() retorna (pa_instance, audio_out) onde:
        #   - audio_out é uma TUPLA ("paplay"|"pw-cat", proc) no Linux CLI
        #   - audio_out é um pyaudio.Stream no Windows/macOS
        #   - audio_out é None se falhou
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


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def get_local_ips() -> list[str]:
    """Lista os IPs IPv4 não-loopback do PC para o celular conectar."""
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

    # Fallback: cria socket UDP para descobrir IP padrão de saída
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            ips.append("<seu-ip-aqui>")

    return ips


def ensure_ssl_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Gera um certificado SSL auto-assinado se não existir.

    Tenta primeiro via biblioteca `cryptography` (mais portátil), e faz
    fallback para o comando `openssl` se ela não estiver instalada.

    Retorna (caminho_cert, caminho_key).
    """
    cert_file = cert_dir / "phonecam.pem"
    key_file = cert_dir / "phonecam.key"

    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    cert_dir.mkdir(parents=True, exist_ok=True)

    # Coleta IPs locais para incluir no SAN (Subject Alternative Name)
    san_ips = [ipaddress.ip_address("127.0.0.1")]
    for ip in get_local_ips():
        try:
            san_ips.append(ipaddress.ip_address(ip))
        except ValueError:
            pass

    # ---- Caminho 1: biblioteca cryptography (preferido) ----
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        print(">>> Gerando certificado SSL auto-assinado (cryptography)…")
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

    # ---- Caminho 2: openssl CLI (fallback) ----
    print(">>> Gerando certificado SSL auto-assinado (openssl)…")
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
    """Cria a webcam virtual (multiplataforma via pyvirtualcam).

    Backends por OS:
      - Linux:   v4l2loopback (precisa: sudo modprobe v4l2loopback exclusive_caps=1 video_nr=10)
      - Windows: OBS Virtual Camera (instale o OBS Studio e ative "Start Virtual Camera" uma vez)
      - macOS:   OBS Virtual Camera (instale o OBS Studio e ative "Start Virtual Camera" uma vez)

    pyvirtualcam abstrai o backend automaticamente — só passamos width/height/fps/fmt.
    """
    if not HAS_PYVC:
        print("[AVISO] pyvirtualcam ausente — frames serão descartados (apenas debug).")
        return None

    import inspect

    # ---- Monta kwargs via introspecção da assinatura ----
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

    # ---- Tenta criar a câmera ----
    try:
        cam = pyvirtualcam.Camera(**kwargs)
        state.native_fmt = native_fmt
        print(f"[OK] Webcam virtual criada em: {cam.device}")
        print(f"     Resolução: {width}x{height} @ {fps}fps")
        print(f"     OS: {OS_NAME} | pyvirtualcam {getattr(pyvirtualcam, '__version__', '?')}")
        print(f"     (kwargs: {sorted(kwargs.keys())}, fmt: {native_fmt})")
        return cam
    except TypeError as e:
        print(f"[AVISO] Assinatura pyvirtualcam incompatível ({e}); tentando fallbacks…")
        last_err = e
    except RuntimeError as e:
        # Dispositivo não existe — mostra instruções específicas do OS
        print()
        print("[ERRO] Não foi possível criar a webcam virtual.")
        print(f"       Causa: {e}")
        print()
        _print_webcam_setup_instructions()
        return None
    except Exception as e:
        last_err = e

    # ---- Fallbacks (só se TypeError) ----
    fallbacks = [
        (dict(width=width, height=height, fps=fps, fmt=pyvirtualcam.PixelFormat.BGR), "BGR"),
        (dict(width=width, height=height, fps=fps, fmt=pyvirtualcam.PixelFormat.RGB), "RGB"),
        (dict(width=width, height=height, fps=fps, fmt=pyvirtualcam.PixelFormat.BGR, delay=0), "BGR"),
        (dict(width=width, height=height, fps=fps, fourcc=0x32424752), "BGR"),
    ]

    for fb_kwargs, fmt in fallbacks:
        try:
            cam = pyvirtualcam.Camera(**fb_kwargs)
            state.native_fmt = fmt
            print(f"[OK] Webcam virtual criada em: {cam.device}")
            print(f"     Resolução: {width}x{height} @ {fps}fps")
            print(f"     Backend: pyvirtualcam (fallback, kwargs: {sorted(fb_kwargs.keys())}, fmt: {fmt})")
            return cam
        except TypeError:
            continue
        except RuntimeError as e:
            print()
            print("[ERRO] Não foi possível criar a webcam virtual.")
            print(f"       Causa: {e}")
            print()
            _print_webcam_setup_instructions()
            return None
        except Exception as e:
            last_err = e
            continue

    print()
    print("[ERRO] Não foi possível criar a webcam virtual.")
    print(f"       Causa: {last_err}")
    print()
    _print_webcam_setup_instructions()
    return None


def _print_webcam_setup_instructions():
    """Mostra instruções de setup da webcam virtual conforme o OS."""
    print("Instruções de setup para webcam virtual:")
    print()
    if IS_LINUX:
        print("  Linux — carregue o módulo v4l2loopback:")
        print()
        print("    sudo modprobe v4l2loopback exclusive_caps=1 \\")
        print("         video_nr=10 card_label=\"PhoneCam\"")
        print()
        print("  Instalação do módulo:")
        print("    Arch Linux:    sudo pacman -S v4l2loopback-dkms")
        print("    Ubuntu/Debian: sudo apt install v4l2loopback-dkms")
        print("    Fedora:        sudo dnf install v4l2loopback")
    elif IS_WINDOWS:
        print("  Windows — instale o OBS Studio (gratuito):")
        print("    https://obsproject.com/download")
        print()
        print("  Após instalar, ABRA o OBS Studio uma vez e inicie a")
        print("  'Virtual Camera' (botão 'Start Virtual Camera' no painel").lstrip()
        print("  de controles). Isso registra a DLL da câmera virtual no Windows.")
        print()
        print("  Depois feche o OBS — a DLL continua registrada.")
        print()
        print("  Alternativa sem OBS: instalar 'Unity Capture' ou 'OBS-VirtualCam'")
        print("  standalone (procure no GitHub).")
    elif IS_MACOS:
        print("  macOS — instale o OBS Studio (gratuito):")
        print("    https://obsproject.com/download")
        print()
        print("  Após instalar, ABRA o OBS Studio uma vez e inicie a")
        print("  'Virtual Camera'. Isso registra o plugin de câmera virtual.")
        print()
        print("  Nota: no macOS Sonoma+ pode ser necessário conceder permissão")
        print("  de câmera ao OBS em System Settings → Privacy & Security → Camera.")
    else:
        print(f"  OS não reconhecido: {OS_NAME}")
    print()


# --------------------------------------------------------------------------- #
# Microfone virtual (PulseAudio / PipeWire)
# --------------------------------------------------------------------------- #
# Estratégia: criar um "null sink" no PulseAudio (ou PipeWire via pactl)
# com nome "PhoneCam Mic" e monitor-lo. O pyaudio abre o stream de output
# nesse sink. Aplicativos (Discord, OBS) selecionam "PhoneCam Mic Monitor"
# como dispositivo de captura para receber o áudio do celular.
#
# PulseAudio:
#   pacmd load-module module-null-sink sink_name=phonecam_mic \
#       sink_properties=device.description="PhoneCam Mic"
#
# PipeWire (com pactl que fala o protocolo pipewire-pulse):
#   Mesmo comando funciona, mas também cria automaticamente um monitor.

AUDIO_SAMPLE_RATE = 48000   # 48kHz — padrão para chamadas/streams
AUDIO_CHANNELS = 1          # mono
AUDIO_CHUNK_MS = 20         # 20ms por chunk -> 50 chunks/s (latência baixa)


def setup_virtual_mic():
    """Cria microfone virtual (multiplataforma).

    Estratégia por OS:
      - Linux:   cria null sink no PulseAudio/PipeWire via `pactl`
      - macOS:   NÃO cria automaticamente — usuário precisa instalar BlackHole
                 (https://existential.audio/blackhole/). PyAudio abre o dispositivo.
      - Windows: NÃO cria automaticamente — usuário precisa instalar VB-Cable
                 (https://vb-audio.com/Cable/). PyAudio abre o dispositivo.

    Retorna (pyaudio_instance, pyaudio_stream) ou (None, None) se falhar.
    """
    if not HAS_PYAUDIO:
        print("[AVISO] pyaudio não instalado — áudio do celular desativado.")
        print("        Instale com: pip install pyaudio")
        if IS_LINUX:
            print("        E no Arch: sudo pacman -S portaudio")
            print("        Ubuntu:     sudo apt install portaudio19-dev")
            print("        Fedora:     sudo dnf install portaudio-devel")
        elif IS_MACOS:
            print("        E no Mac:   brew install portaudio")
        elif IS_WINDOWS:
            print("        No Windows o pyaudio geralmente já vem com wheel pré-compilado.")
        return None, None

    if IS_LINUX:
        return _setup_mic_linux()
    elif IS_MACOS:
        return _setup_mic_macos()
    elif IS_WINDOWS:
        return _setup_mic_windows()
    else:
        print(f"[AVISO] OS não suportado para áudio: {OS_NAME}")
        return None, None


def _setup_mic_linux():
    """Linux: cria null sink + virtual source para o Discord ver como microfone.

    Arquitetura:
      null sink "phonecam_mic_sink" (OUTPUT) ← pw-cat toca PCM aqui
              ↓ (monitor interno)
      virtual source "PhoneCam Mic" (INPUT)  ← Discord/OBS seleciona como microfone

    Isso resolve o problema do Discord não listar "Monitor of ..." como
    dispositivo de entrada. Com virtual source, o Discord vê "PhoneCam Mic"
    direto como um microfone.
    """
    sink_name = "phonecam_mic_sink"        # nome interno do sink (output)
    source_name = "phonecam_mic"           # nome interno do source (input)
    source_desc = "PhoneCam Mic"           # descrição amigável (aparece no Discord)

    if not _has_pulseaudio():
        print("[AVISO] Nenhum servidor PulseAudio/PipeWire detectado — áudio desativado.")
        return None, None

    print(f">>> Criando microfone virtual '{source_desc}' no PulseAudio/PipeWire…")
    # 1) Cria null sink (onde o pw-cat vai tocar o áudio)
    _create_null_sink_retryable(sink_name, "PhoneCam Mic Sink")
    # 2) Cria virtual source (o que apps vão selecionar como microfone)
    _create_virtual_source_retryable(source_name, source_desc, f"{sink_name}.monitor")

    # Toca áudio no null sink via pw-cat/paplay
    return _setup_mic_linux_fallback_paplay(sink_name, source_desc)


def _setup_mic_linux_fallback_paplay(sink_name: str, sink_desc: str):
    """Usa pw-cat (PipeWire nativo) ou paplay (PulseAudio CLI) para tocar PCM.

    Prioridade:
      1. pw-cat (nativo do PipeWire, mais confiável no Arch/Fedora modernos)
      2. paplay (compatibilidade PulseAudio, funciona em sistemas mais antigos)
    """
    import shutil
    import threading

    pwcat = shutil.which("pw-cat")
    paplay = shutil.which("paplay")

    if pwcat:
        # pw-cat --playback --target phonecam_mic --format s16 --rate 48000 --channels 1 --raw
        # --target diz ao pw-cat qual sink usar (por nome)
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
            "--volume=65536",   # volume máximo (PulseAudio usa 0-65536)
        ]
        tool_name = "paplay"
    else:
        print("[!] Nem 'pw-cat' nem 'paplay' encontrados no PATH.")
        print("    Instale um deles:")
        print("      Arch (PipeWire): sudo pacman -S pipewire")
        print("      Ubuntu:          sudo apt install pulseaudio-utils")
        print("      Fedora:          sudo dnf install pipewire pulseaudio-utils")
        return None, None

    print(f">>> Iniciando {tool_name}…")
    print(f"    comando: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        print(f"[!] Não foi possível iniciar {tool_name}: {e}")
        return None, None

    # Thread para ler stderr e logar (para diagnóstico)
    def _log_stderr():
        try:
            for line in iter(proc.stderr.readline, b""):
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    print(f"[{tool_name}] {msg}")
        except Exception:
            pass
    threading.Thread(target=_log_stderr, daemon=True, name=f"{tool_name}-stderr").start()

    # Verifica se o processo está vivo
    import time as _time
    _time.sleep(0.5)
    if proc.poll() is not None:
        try:
            err = proc.stderr.read().decode("utf-8", errors="replace").strip()
        except Exception:
            err = ""
        print(f"[!] {tool_name} terminou imediatamente (exit code {proc.returncode})")
        if err:
            print(f"    stderr: {err}")
        # Se pw-cat falhou, tenta paplay como fallback
        if tool_name == "pw-cat" and paplay:
            print("    Tentando paplay como fallback…")
            return _setup_mic_linux_fallback_paplay_paplay_only(sink_name, sink_desc, paplay)
        return None, None

    print(f"[OK] Microfone virtual criado: '{sink_desc}' (via {tool_name})")
    print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
    print(f"     Use 'Monitor of {sink_desc}' como dispositivo de captura em Discord/OBS/Zoom.")
    # Retorna (pa_instance=None, audio_out=tuple)
    # audio_out é uma tupla (tool_name, proc) para o write_audio_chunk detectar
    return (None, (tool_name, proc))


def _setup_mic_linux_fallback_paplay_paplay_only(sink_name: str, sink_desc: str, paplay: str):
    """Fallback: usa apenas paplay (quando pw-cat falha)."""
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
    print(f">>> Iniciando paplay (fallback)…")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except Exception as e:
        print(f"[!] Não foi possível iniciar paplay: {e}")
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
        print(f"[!] paplay terminou imediatamente (exit code {proc.returncode})")
        if err:
            print(f"    stderr: {err}")
        return None, None

    print(f"[OK] Microfone virtual criado: '{sink_desc}' (via paplay)")
    print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
    print(f"     Use 'Monitor of {sink_desc}' como dispositivo de captura em Discord/OBS/Zoom.")
    return (None, ("paplay", proc))


def _setup_mic_macos():
    """macOS: usuário instala BlackHole 2ch (https://existential.audio/blackhole/).

    PyAudio então abre o BlackHole como output device.
    Apps selecionam "BlackHole 2ch" como microfone.
    """
    print(">>> macOS: procurando BlackHole (instale de https://existential.audio/blackhole/)…")
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:
        print(f"[!] Erro ao inicializar PyAudio: {e}")
        return None, None

    # Procura por dispositivo BlackHole
    blackhole_idx = _find_device_by_name(pa, "BlackHole", output=True)
    if blackhole_idx is None:
        print("[!] BlackHole não encontrado. Instale:")
        print("    1) Baixe em: https://existential.audio/blackhole/")
        print("    2) Instale o pacote .pkg")
        print("    3) Reinicie o servidor de áudio: sudo killall coreaudiod")
        print("    4) Rode novamente: ./run.py --audio")
        print()
        print("    Alternativa: Loopback (https://rogueamoeba.com/loopback/) — pago.")
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
        print(f"[OK] Saída de áudio aberta em BlackHole (device #{blackhole_idx})")
        print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
        print(f"     Use 'BlackHole 2ch' como dispositivo de captura em Discord/OBS/Zoom.")
        return pa, stream
    except Exception as e:
        print(f"[!] Erro ao abrir BlackHole: {e}")
        return pa, None


def _setup_mic_windows():
    """Windows: usuário instala VB-Cable (https://vb-audio.com/Cable/).

    PyAudio abre o "CABLE Input" como output. Apps selecionam "CABLE Output"
    como microfone.
    """
    print(">>> Windows: procurando VB-Cable (instale de https://vb-audio.com/Cable/)…")
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:
        print(f"[!] Erro ao inicializar PyAudio: {e}")
        return None, None

    # Procura por "CABLE Input" (VB-Audio Virtual Cable)
    cable_idx = _find_device_by_name(pa, "CABLE Input", output=True)
    if cable_idx is None:
        # Tenta também "VB-Audio"
        cable_idx = _find_device_by_name(pa, "VB-Audio", output=True)
    if cable_idx is None:
        print("[!] VB-Cable não encontrado. Instale:")
        print("    1) Baixe em: https://vb-audio.com/Cable/")
        print("    2) Descompacte e rode VBCABLE_Setup_x64.exe como administrador")
        print("    3) Reinicie o PC")
        print("    4) Rode novamente: python run.py --audio")
        print()
        print("    Alternativa: VoiceMeeter (https://vb-audio.com/Voicemeeter/) — mais recursos.")
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
        print(f"[OK] Saída de áudio aberta em VB-Cable Input (device #{cable_idx})")
        print(f"     Sample rate: {AUDIO_SAMPLE_RATE}Hz, mono, 16-bit PCM")
        print(f"     Use 'CABLE Output' como dispositivo de captura em Discord/OBS/Zoom.")
        return pa, stream
    except Exception as e:
        print(f"[!] Erro ao abrir VB-Cable: {e}")
        return pa, None


def _find_device_by_name(pa, name_pattern: str, output: bool = True) -> int | None:
    """Procura um device cujo nome contenha name_pattern (case-insensitive)."""
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
        print(f"[!] Erro ao procurar device: {e}")
    return None


def _has_pulseaudio() -> bool:
    """Verifica se pactl está disponível e consegue falar com o servidor."""
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
    """Cria o null sink de forma idempotente.

    Se já existe um sink com esse nome, descarrega e recria para garantir
    que o formato (channels/rate) está correto.
    """
    # 1) Verifica se já existe e descarrega se existir (para recriar com formato correto)
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "modules"],
            capture_output=True, text=True, timeout=3,
        )
        # Procura o module-null-sink com nosso sink_name e descarrega
        for line in r.stdout.split("\n"):
            if "module-null-sink" in line and f"sink_name={sink_name}" in line:
                module_id = line.split()[0]
                print(f"    (descarregando módulo antigo #{module_id} para recriar com formato correto)")
                subprocess.run(["pactl", "unload-module", module_id],
                              capture_output=True, timeout=3)
                break
    except Exception:
        pass

    # 2) Cria via module-null-sink com formato explícito
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
            print(f"[!] pactl falhou: {r.stderr.strip() or r.stdout.strip()}")
        else:
            print(f"    [OK] sink criado (module id: {r.stdout.strip()})")
    except Exception as e:
        print(f"[!] Erro ao criar null sink: {e}")


def _create_virtual_source_retryable(source_name: str, source_desc: str, master: str):
    """Cria um virtual source (INPUT device) que lê do master source.

    Tenta duas abordagens:
    1. module-virtual-source (PulseAudio clássico) — funciona na maioria dos sistemas
    2. pw-loopback (PipeWire nativo) — alternativa mais moderna

    Args:
        source_name: nome interno (ex: "phonecam_mic")
        source_desc: descrição amigável (ex: "PhoneCam Mic")
        master: source master para ler (ex: "phonecam_mic_sink.monitor")
    """
    # 1) Verifica se já existe e descarrega
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "modules"],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.split("\n"):
            if "module-virtual-source" in line and f"source_name={source_name}" in line:
                module_id = line.split()[0]
                print(f"    (descarregando virtual source antigo #{module_id})")
                subprocess.run(["pactl", "unload-module", module_id],
                              capture_output=True, timeout=3)
                break
    except Exception:
        pass

    # 2) Cria via module-virtual-source
    # Props importantes para PipeWire reconhecer como microfone:
    #   - device.description: nome amigável
    #   - media.class: Audio/Source (necessário no PipeWire)
    #   - device.icon_name: aparece com ícone de microfone
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
            print(f"[!] module-virtual-source falhou: {r.stderr.strip() or r.stdout.strip()}")
            print(f"    Tentando pw-loopback (PipeWire nativo)…")
            if _create_pw_loopback(source_name, source_desc, master):
                print(f"    [OK] pw-loopback criado")
            else:
                print(f"    [!] pw-loopback também falhou")
                print(f"    Alternativa: use 'Monitor of {master.split('.')[0]}' como microfone")
        else:
            print(f"    [OK] virtual source criado (module id: {r.stdout.strip()})")
            print(f"    Selecione '{source_desc}' como dispositivo de ENTRADA no Discord/OBS/Zoom")
    except Exception as e:
        print(f"[!] Erro ao criar virtual source: {e}")


def _create_pw_loopback(source_name: str, source_desc: str, master: str) -> bool:
    """Cria um source virtual via pw-loopback (PipeWire nativo).

    pw-loopback cria um nodo que captura de `master` e reproduz em um
    novo nodo virtual. Para que esse nodo apareça como INPUT device
    (microfone) no Discord, configuramos media.class=Audio/Source.
    """
    import shutil
    import json as _json

    pw_loopback = shutil.which("pw-loopback")
    if not pw_loopback:
        return False

    # Props do nodo de playback (que vira o "microfone virtual")
    playback_props = _json.dumps({
        "node.name": source_name,
        "node.description": source_desc,
        "media.class": "Audio/Source",
        "device.icon_name": "audio-input-microphone",
        "device.description": source_desc,
    })
    # Props do nodo de capture (escondido)
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

    print(f"    comando pw-loopback: {' '.join(cmd[:4])} ...")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        import time as _t
        _t.sleep(0.8)
        if proc.poll() is None:
            # Salva o proc para cleanup no shutdown
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
        print(f"    [!] Exceção ao iniciar pw-loopback: {e}")
        return False


def _find_sink_device(pa, sink_desc: str) -> int | None:
    """Encontra o device index (PyAudio) do monitor do sink 'PhoneCam Mic'.

    PyAudio lista cada sink + seu monitor como devices separados.
    Procuramos pelo device de OUTPUT chamado 'PhoneCam Mic' — escrever nele
    faz o som ir para o null sink, e o monitor correspondente é o que os apps
    selecionam como "microfone".
    """
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            name = info.get("name", "")
            desc = info.get("name", "")  # PyAudio traz name, não description
            # Procura tanto por sink_name quanto por descrição
            if "phonecam" in name.lower() or "phonecam" in desc.lower():
                # Verifica se é output (maxOutputChannels > 0)
                if info.get("maxOutputChannels", 0) > 0:
                    return i
    except Exception as e:
        print(f"[!] Erro ao procurar sink: {e}")
    return None


def _is_cli_audio(state_obj) -> bool:
    """Verifica se o audio_out é um subprocess CLI (pw-cat ou paplay)."""
    return (
        isinstance(state_obj, tuple)
        and len(state_obj) == 2
        and state_obj[0] in ("pw-cat", "paplay")
    )


def teardown_audio():
    """Fecha stream e instância PyAudio. Não descarrega o módulo (outros
    apps podem estar usando o monitor)."""
    # Mata pw-loopback se existir
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

    # Caso especial: subprocess CLI (pw-cat ou paplay)
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

    # Caminho normal: PyAudio
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
    """Escreve um chunk PCM no dispositivo de áudio virtual.

    Retorna True se OK, False se houve erro (deve fechar conexão).
    Lida com ambos os caminhos: PyAudio stream e CLI subprocess (pw-cat/paplay).
    """
    if state.audio_out is None:
        return False

    # Caso especial: subprocess CLI (pw-cat ou paplay)
    if _is_cli_audio(state.audio_out):
        tool_name, proc = state.audio_out
        try:
            if proc.poll() is not None:
                print(f"[!] {tool_name} morreu (exit code {proc.returncode})")
                # Tenta reiniciar
                if _restart_paplay():
                    print(f"[OK] {tool_name} reiniciado — tentando escrever novamente")
                    _, proc = state.audio_out
                    proc.stdin.write(data)
                    proc.stdin.flush()
                    return True
                return False
            proc.stdin.write(data)
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError) as e:
            print(f"[!] Erro ao escrever no stdin do {tool_name}: {type(e).__name__}: {e}")
            # CLI provavelmente morreu — tenta reiniciar
            if _restart_paplay():
                print(f"[OK] reiniciado — tentando escrever novamente")
                try:
                    _, proc = state.audio_out
                    proc.stdin.write(data)
                    proc.stdin.flush()
                    return True
                except Exception as e2:
                    print(f"[!] Falha mesmo após reiniciar: {e2}")
            return False
        except Exception as e:
            print(f"[!] Erro inesperado ao escrever áudio: {type(e).__name__}: {e}")
            return False

    # Caminho normal: PyAudio
    try:
        state.audio_out.write(data)
        return True
    except Exception as e:
        print(f"[!] Erro ao escrever no PyAudio stream: {e}")
        return False


def _restart_paplay() -> bool:
    """Reinicia o subprocess CLI (pw-cat ou paplay) se ele morreu."""
    if not IS_LINUX:
        return False
    try:
        # Mata o processo antigo se ainda estiver rodando
        if _is_cli_audio(state.audio_out):
            _, old_proc = state.audio_out
            try:
                if old_proc.poll() is None:
                    old_proc.terminate()
                    old_proc.wait(timeout=2)
            except Exception:
                pass

        # Cria novo subprocess tocando no null sink (não no virtual source)
        # _setup_mic_linux_fallback_paplay retorna (None, (tool_name, proc))
        pa_instance, audio_out = _setup_mic_linux_fallback_paplay("phonecam_mic_sink", "PhoneCam Mic")
        if audio_out is not None:
            state.audio_pa = pa_instance
            state.audio_out = audio_out
            return True
        return False
    except Exception as e:
        print(f"[!] Erro ao reiniciar CLI de áudio: {e}")
        return False


def qr_ascii(url: str, compact: bool = True) -> str:
    """Gera um QR Code em ASCII art para mostrar no terminal.

    Usa blocos unicode ' █' (espaço + bloco) para representar cada módulo.
    Em terminais modernos isso renderiza como um QR Code escaneável.
    """
    if not HAS_QRCODE:
        return "[QR Code indisponível — instale: pip install qrcode[pil]]"

    qr = qrcode.QRCode(
        version=None,                  # auto
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # Renderiza como matriz booleana
    matrix = qr.get_matrix()
    h = len(matrix)
    w = len(matrix[0]) if h else 0

    # Compacta 2 linhas por linha textual usando half-blocks (▀▄█)
    # Isso reduz a altura pela metade e fica legível em terminais pequenos
    lines = []
    for y in range(0, h, 2):
        row = []
        for x in range(w):
            top = matrix[y][x] if y < h else False
            bot = matrix[y + 1][x] if (y + 1) < h else False
            # Combina bits: top=UPPER, bot=LOWER
            # Usa caracteres half-block:
            #   ▀ = só topo preto
            #   ▄ = só baixo preto
            #   █ = ambos pretos
            #   espaço = ambos brancos
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
    """Gera um QR Code como PNG (retorna bytes)."""
    if not HAS_QRCODE:
        raise RuntimeError("qrcode não instalado")

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
    print("  PhoneCam — Celular como Webcam no PC")
    print("=" * 64)
    print()
    print(f"  OS        : {OS_NAME}")
    print(f"  Resolução : {args.width}x{args.height} @ {args.fps}fps")
    print(f"  PIN acesso: {pin}")
    print(f"  Protocolo : {scheme.upper()}")
    if cert_file and not args.no_https:
        print(f"  Cert SSL  : {cert_file}")
    print(f"  Microfone : {'ATIVADO' if args.audio else 'desativado (--no-audio)'}")
    print()

    # QR Code aponta para a primeira URL com PIN embutido (auto-conexão)
    base_host = ips[0] if ips else "localhost"
    qr_url = f"{scheme}://{base_host}:{args.port}/?pin={pin}"
    if HAS_QRCODE:
        print("  ┌─ Escaneie o QR Code com a câmera do celular ─┐")
        print("  │  (o PIN já vem embutido — conecta sozinho)   │")
        print("  └──────────────────────────────────────────────┘")
        print()
        ascii_qr = qr_ascii(qr_url)
        # Indenta o QR Code para ficar alinhado no banner
        for line in ascii_qr.split("\n"):
            print("      " + line)
        print()
        print(f"  URL embutida no QR: {qr_url}")
        print()
    else:
        print("  1) No celular, conectado na MESMA rede Wi-Fi que o PC,")
        print(f"     abra uma das URLs abaixo no navegador (Chrome/Safari/Firefox):")
        print()
        for ip in ips:
            print(f"        {scheme}://{ip}:{args.port}/?pin={pin}")
        print()
        print("  (Instale 'qrcode' para ver QR Code: pip install qrcode[pil])")
        print()

    if not args.no_https:
        print("  ⚠  AVISO DE CERTIFICADO: o navegador vai mostrar 'Conexão não")
        print("     segura'. É NORMAL — aceite para continuar:")
        print("       Chrome Android: 'Avançado' → 'Continuar para <ip> (não seguro)'")
        print("       Safari iOS:     'Mostrar detalhes' → 'Visitar este site'")
        print()
    print(f"  2) Digite o PIN: {pin}  (ou escaneie o QR acima p/ pular esta etapa)")
    print()
    print("  3) A webcam virtual aparecerá em /dev/video10 (ou outro /dev/videoN).")
    print("     Selecione \"PhoneCam\" em Discord, OBS, Zoom, etc.")
    print()
    print("-" * 64)
    print("Logs (Ctrl+C para encerrar):")
    print()


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
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
    """Imagem PNG do QR Code apontando para a URL principal do PhoneCam (com PIN embutido)."""
    from fastapi import Response
    if not HAS_QRCODE:
        return JSONResponse({"error": "qrcode não instalado"}, status_code=503)
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
    """QR Code em ASCII art (para inspecionar via curl). Inclui PIN na URL."""
    from fastapi import Response
    if not HAS_QRCODE:
        return JSONResponse({"error": "qrcode não instalado"}, status_code=503)
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
    """Recebe frames JPEG binários do celular e escreve na webcam virtual."""
    cfg = app.state.cfg

    # Valida PIN antes de aceitar
    if pin != app.state.pin:
        await ws.close(code=4003, reason="PIN inválido")
        return

    # Apenas um cliente ativo por vez
    async with state.client_lock:
        if state.client is not None and state.client is not ws:
            try:
                await state.client.close(code=4009, reason="Outro cliente conectado")
            except Exception:
                pass
        state.client = ws
        state.client_addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
        state.connected_since = time.time()

    await ws.accept()
    print(f"[+] Cliente conectado: {state.client_addr}")

    # Timeout de inatividade: se nenhum frame chegar em 15s, considera conexão morta
    INACTIVITY_TIMEOUT = 15.0

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_bytes(), timeout=INACTIVITY_TIMEOUT)
            except asyncio.TimeoutError:
                # Sem frames por 15s — pode ser tela apagada ou conexão presa
                print(f"\n[!] Sem frames por {INACTIVITY_TIMEOUT:.0f}s — fechando conexão")
                break

            try:
                img = Image.open(io.BytesIO(msg)).convert("RGB")
            except Exception as e:
                print(f"[!] Frame inválido ({e}), descartado")
                continue

            # Redimensiona se necessário
            if img.size != (cfg.width, cfg.height):
                img = img.resize((cfg.width, cfg.height), Image.LANCZOS)

            # Monta o array numpy no formato esperado pela câmera virtual
            # Detectado durante init_virtual_cam (RGB ou BGR conforme backend)
            arr = np.asarray(img)
            if state.native_fmt == "BGR":
                arr = arr[:, :, ::-1]  # RGB -> BGR
            # se RGB, mantém como está

            if state.cam is not None:
                try:
                    state.cam.send(arr)
                except Exception as e:
                    print(f"[!] Erro ao enviar frame para v4l2: {e}")

            # Estatísticas de FPS
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
                    f"Cliente: {state.client_addr}    ",
                    end="",
                    flush=True,
                )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"\n[!] Erro no WebSocket: {e}")
    finally:
        async with state.client_lock:
            if state.client is ws:
                state.client = None
                state.client_addr = ""
        print(f"\n[-] Cliente desconectado: {ws.client}")


@app.get("/audio-test")
async def audio_test():
    """Grava 3 segundos de áudio do mic virtual (monitor) e retorna como WAV.

    Útil para testar se o áudio do celular está realmente chegando no sink.
    Acesse https://<ip>:<port>/audio-test no navegador do PC para baixar o WAV.
    """
    import tempfile
    import shutil
    from fastapi import Response

    sink_name = "phonecam_mic_sink"
    # Grava do virtual source "phonecam_mic" (não do monitor do sink)
    # porque é isso que o Discord vê como dispositivo de entrada
    monitor_name = "phonecam_mic"
    duration_s = 3

    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav.close()

    pw_record = shutil.which("pw-record")
    parec = shutil.which("parec")

    if not (pw_record or parec):
        return JSONResponse(
            {"error": "nem pw-record nem parec disponíveis"},
            status_code=500,
        )

    if pw_record:
        cmd = [pw_record, "--format", "s16", "--rate", str(AUDIO_SAMPLE_RATE),
               "--channels", str(AUDIO_CHANNELS), "-d", monitor_name, tmp_wav.name]
    else:
        # parec com --file-format=wav
        cmd = ["parec", "--format=s16le", f"--rate={AUDIO_SAMPLE_RATE}",
               f"--channels={AUDIO_CHANNELS}", f"-d={monitor_name}",
               f"--file-format=wav", f"--file={tmp_wav.name}"]

    # Roda por 3s e mata o processo
    import subprocess as sp
    try:
        proc = sp.Popen(cmd, stdout=sp.DEVNULL, stderr=sp.PIPE)
        try:
            proc.wait(timeout=duration_s)
        except sp.TimeoutExpired:
            # Esperado — o processo grava indefinidamente, matamos após 3s
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except sp.TimeoutExpired:
                proc.kill()
                proc.wait()

        if proc.returncode and proc.returncode > 0:
            err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            return JSONResponse(
                {"error": f"gravação falhou (exit {proc.returncode}): {err}"},
                status_code=500,
            )

        if not os.path.exists(tmp_wav.name) or os.path.getsize(tmp_wav.name) == 0:
            return JSONResponse(
                {"error": "arquivo WAV vazio — sink/monitor não encontrado ou sem áudio"},
                status_code=500,
            )

        wav_data = open(tmp_wav.name, "rb").read()
        return Response(
            content=wav_data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=phonecam-audio-test.wav",
                "Cache-Control": "no-store",
            },
        )
    finally:
        try:
            os.unlink(tmp_wav.name)
        except Exception:
            pass


@app.websocket("/ws/audio")
async def ws_audio_endpoint(ws: WebSocket, pin: str = Query(...)):
    """Recebe áudio PCM 16-bit mono do celular e toca no microfone virtual.

    Formato: chunks binários de PCM 16-bit signed little-endian, mono, 48kHz.
    Latência alvo: ~20ms por chunk.
    """
    if pin != app.state.pin:
        await ws.close(code=4003, reason="PIN inválido")
        return

    if not state.audio_enabled or state.audio_out is None:
        await ws.close(code=4004, reason="Áudio desativado no servidor (use --audio)")
        return

    await ws.accept()
    print(f"[+] Cliente de áudio conectado: {ws.client.host if ws.client else '?'}")

    # Estatísticas de volume para diagnóstico
    import struct
    last_stats_time = time.time()
    stats_packets = 0
    max_sample_seen = 0

    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_bytes(), timeout=60.0)
            except asyncio.TimeoutError:
                break

            ok = write_audio_chunk(msg)
            if not ok:
                print(f"\n[!] Erro ao escrever áudio no sink — fechando conexão")
                break
            state.audio_packets += 1
            stats_packets += 1

            # A cada 2s, mede volume RMS do último chunk para diagnóstico
            now = time.time()
            if now - last_stats_time >= 2.0 and len(msg) >= 2:
                try:
                    # Calcula pico absoluto dos samples Int16
                    n = len(msg) // 2
                    samples = struct.unpack(f"<{n}h", msg[:n*2])
                    if samples:
                        peak = max(abs(s) for s in samples)
                        max_sample_seen = max(max_sample_seen, peak)
                        # Converte para dBFS (0 dBFS = 32767)
                        dbfs = (20 * math.log10(peak / 32767)) if peak > 0 else -96.0
                        print(
                            f"\r[áudio] pacotes: {state.audio_packets} | "
                            f"pico atual: {dbfs:6.1f} dBFS | "
                            f"pico máx: {max_sample_seen:5d}/32767    ",
                            end="", flush=True,
                        )
                except Exception:
                    pass
                last_stats_time = now
                stats_packets = 0
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"\n[!] Erro no WS de áudio: {e}")
    finally:
        print(f"\n[-] Cliente de áudio desconectado ({state.audio_packets} pacotes totais)")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description="PhoneCam — celular como webcam + microfone virtual (Windows/macOS/Linux).",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="Porta TCP (default: 8765)")
    parser.add_argument("--width", type=int, default=1280, help="Largura (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="Altura (default: 720)")
    parser.add_argument("--fps", type=int, default=30, help="FPS alvo (default: 30)")
    parser.add_argument("--pin", default=None, help="PIN fixo (default: aleatório a cada execução)")
    parser.add_argument(
        "--no-https",
        action="store_true",
        help="Desativa HTTPS (uso só em localhost; navegadores bloqueiam getUserMedia em HTTP remoto)",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        default=True,
        help="Ativa microfone do celular (padrão: LIGADO). Use --no-audio para desativar.",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Desativa microfone do celular (mantém só vídeo)",
    )
    args = parser.parse_args()

    # --no-audio sobrescreve --audio (que agora é default True)
    if args.no_audio:
        args.audio = False

    pin = args.pin if args.pin else f"{secrets.randbelow(1_000_000):06d}"
    app.state.cfg = args
    app.state.pin = pin

    # Gera certificado SSL auto-assinado (necessário para getUserMedia no celular)
    cert_file = None
    key_file = None
    if not args.no_https:
        try:
            cert_file, key_file = ensure_ssl_cert(BASE_DIR / "certs")
        except Exception as e:
            print(f"[AVISO] Não foi possível gerar cert SSL ({e}); iniciando em HTTP.")
            print("        getUserMedia pode falhar no celular. Use --no-https só em localhost.")
            args.no_https = True

    banner(args, pin, cert_file)

    try:
        if args.no_https:
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        else:
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                log_level="warning",
                ssl_certfile=str(cert_file),
                ssl_keyfile=str(key_file),
            )
    except KeyboardInterrupt:
        print("\n\n[+] Encerrando PhoneCam...")
    finally:
        if state.cam:
            try:
                state.cam.close()
            except Exception:
                pass
        teardown_audio()


if __name__ == "__main__":
    main()
