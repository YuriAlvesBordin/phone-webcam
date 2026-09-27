"""WebRTC session: receives the phone's media tracks (aiortc) and feeds
the virtual webcam and virtual microphone.

One ``RTCSession`` == one phone. Video frames are converted off-thread and
sent to pyvirtualcam; audio frames are mixed down to mono s16le 48 kHz and
written to the platform AudioSink.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from phonecam.config import AUDIO_SAMPLE_RATE, INACTIVITY_TIMEOUT_S, STATS_INTERVAL_S
from phonecam.dependencies import HAS_AIORTC

if HAS_AIORTC:
    from aiortc import RTCConfiguration, RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import MediaStreamError

if TYPE_CHECKING:
    from fastapi import WebSocket

    from phonecam.audio import AudioSink
    from phonecam.config import Config
    from phonecam.virtualcam import VirtualCamera

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- audio


def mixdown_to_mono_s16(arr: np.ndarray, channels: int, planar: bool) -> np.ndarray:
    """Average an int16 PCM array down to mono.

    ``planar`` arrays come in as (channels, samples); packed ones as a flat
    interleaved stream (or (1, n*channels)).
    """
    if channels <= 1:
        return np.ascontiguousarray(arr.reshape(-1), dtype=np.int16)
    data = arr
    if planar and data.ndim == 2 and data.shape[0] == channels:
        mixed = data.astype(np.int32).sum(axis=0) // channels
        return mixed.astype(np.int16)
    flat = arr.reshape(-1)
    deinterleaved = np.stack([flat[i::channels] for i in range(channels)])
    mixed = deinterleaved.astype(np.int32).sum(axis=0) // channels
    return mixed.astype(np.int16)


def _resample_nearest(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return data
    n_dst = int(round(len(data) * dst_rate / src_rate))
    idx = np.linspace(0, len(data) - 1, n_dst).astype(np.int64)
    return data[idx]


def audio_frame_to_pcm(frame: object) -> bytes:
    """Convert an av.AudioFrame (aiortc output) to mono s16le bytes."""
    arr = frame.to_ndarray()  # type: ignore[attr-defined]
    if np.issubdtype(arr.dtype, np.floating):
        arr = (np.clip(arr, -1.0, 1.0) * 32767.0).astype(np.int16)
    elif arr.dtype != np.int16:
        arr = arr.astype(np.int16)

    channels = len(frame.layout.channels)  # type: ignore[attr-defined]
    planar = bool(frame.format.is_planar)  # type: ignore[attr-defined]
    mono = mixdown_to_mono_s16(arr, channels, planar)

    src_rate = int(frame.sample_rate)  # type: ignore[attr-defined]
    if src_rate != AUDIO_SAMPLE_RATE:
        mono = _resample_nearest(mono, src_rate, AUDIO_SAMPLE_RATE)
    return mono.tobytes()


# ---------------------------------------------------------------- session


class RTCSession:
    """Binds one signaling WebSocket to one aiortc PeerConnection."""

    def __init__(
        self,
        ws: WebSocket,
        cfg: Config,
        vcam: VirtualCamera | None,
        audio_sink: AudioSink | None,
    ) -> None:
        self.ws = ws
        self.cfg = cfg
        self.vcam = vcam
        self.audio_sink = audio_sink
        self.client_addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
        self._closed = False
        self._tasks: list[asyncio.Task] = []
        self._frames = 0
        self._last_stats_time = time.monotonic()
        self._last_frames = 0
        self._last_bytes = 0
        self.current_fps = 0.0

        self.pc = RTCPeerConnection(RTCConfiguration())

        # The first offer is video-only (audio: false at connect; the mic is
        # renegotiated later), so an audio transceiver declared here would stay
        # unmatched. aiortc >= 1.14 then crashes in setLocalDescription
        # (DIRECTIONS.index(None)) and the connection dies. aiortc creates the
        # audio transceiver implicitly when a renegotiation offer carries it.
        self.pc.addTransceiver("video", direction="recvonly")

        @self.pc.on("track")
        def on_track(track) -> None:
            log.info("[%s] %s track received", self.client_addr, track.kind)
            if track.kind == "video":
                self._spawn(self._consume_video(track))
            elif track.kind == "audio":
                self._spawn(self._consume_audio(track))

        @self.pc.on("connectionstatechange")
        async def on_connection_state() -> None:
            state = self.pc.connectionState
            log.info("[%s] peer connection: %s", self.client_addr, state)
            if state in ("failed", "closed"):
                await self.close()

    # ---------------------------------------------------- signaling API

    async def handle_offer(self, sdp: str) -> str:
        """Apply an offer (initial or renegotiation) and return answer SDP."""
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        answer = await self.pc.createAnswer()
        # aiortc completes ICE gathering inside setLocalDescription on a LAN.
        await self.pc.setLocalDescription(answer)
        self._start_stats_task()
        return self.pc.localDescription.sdp

    async def add_candidate(self, cand: dict) -> None:
        kwargs = {
            k: cand[k]
            for k in ("candidate", "sdpMid", "sdpMLineIndex")
            if k in cand and cand[k] is not None
        }
        if not kwargs.get("candidate"):
            return  # end-of-candidates marker
        await self.pc.addIceCandidate(RTCIceCandidate(**kwargs))

    async def kick(self) -> None:
        """Another client took over: notify and disconnect this one."""
        try:
            await self.ws.send_text(
                json.dumps({"type": "bye", "reason": "another client connected"})
            )
        except Exception:  # noqa: BLE001
            pass
        await self.close(ws_code=4009)

    async def close(self, ws_code: int = 1000) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        try:
            await self.pc.close()
        except Exception:  # noqa: BLE001
            log.debug("Error closing peer connection", exc_info=True)
        try:
            await self.ws.close(code=ws_code)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------- media loops

    async def _consume_video(self, track) -> None:
        log.info("[%s] video consumer started", self.client_addr)
        try:
            while not self._closed:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=INACTIVITY_TIMEOUT_S)
                except asyncio.TimeoutError:
                    log.warning(
                        "[%s] no video frames for %.0fs - closing session",
                        self.client_addr,
                        INACTIVITY_TIMEOUT_S,
                    )
                    await self.close()
                    return
                rgb = await asyncio.to_thread(frame.to_ndarray, format="rgb24")
                await asyncio.to_thread(self._deliver_frame, rgb)
                self._frames += 1
        except MediaStreamError:
            log.info("[%s] video track ended", self.client_addr)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log.error("[%s] video consumer error: %s", self.client_addr, e)

    def _deliver_frame(self, rgb: np.ndarray) -> None:
        """Convert one RGB frame to the virtual camera format and send it."""
        if self.vcam is None:
            return
        if (rgb.shape[1], rgb.shape[0]) != (self.cfg.width, self.cfg.height):
            img = Image.fromarray(rgb).resize((self.cfg.width, self.cfg.height), Image.LANCZOS)
            rgb = np.asarray(img)
        if self.vcam.native_fmt == "BGR":
            rgb = np.ascontiguousarray(rgb[:, :, ::-1])
        self.vcam.send(rgb)

    async def _consume_audio(self, track) -> None:
        log.info("[%s] audio consumer started", self.client_addr)
        try:
            while not self._closed:
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=60.0)
                except asyncio.TimeoutError:
                    log.info("[%s] no audio for 60s - stopping audio consumer", self.client_addr)
                    return
                if self.audio_sink is None:
                    continue  # drain to keep the pipeline moving
                pcm = audio_frame_to_pcm(frame)
                ok = await asyncio.to_thread(self.audio_sink.write, pcm)
                if not ok:
                    log.error("[%s] audio sink write failed - stopping audio", self.client_addr)
                    return
        except MediaStreamError:
            log.info("[%s] audio track ended", self.client_addr)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log.error("[%s] audio consumer error: %s", self.client_addr, e)

    # ---------------------------------------------------- stats

    def _spawn(self, coro) -> None:
        self._tasks.append(asyncio.ensure_future(coro))

    def _start_stats_task(self) -> None:
        if not any(t.get_name() == "stats" for t in self._tasks):
            self._tasks.append(asyncio.create_task(self._stats_loop(), name="stats"))

    async def _stats_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(STATS_INTERVAL_S)
                await self._send_stats()
        except asyncio.CancelledError:
            pass

    async def _send_stats(self) -> None:
        now = time.monotonic()
        dt = now - self._last_stats_time
        if dt <= 0:
            return
        fps = (self._frames - self._last_frames) / dt
        self._last_stats_time = now
        self._last_frames = self._frames
        self.current_fps = fps

        payload: dict = {
            "type": "stats",
            "fps": round(fps, 1),
            "frames": self._frames,
        }
        try:
            for s in (await self.pc.getStats()).values():
                stype = getattr(s, "type", "")
                # aiortc reports received bytes on remote-outbound-rtp.bytesSent
                # (what the phone says it sent); inbound-rtp has no byte counter.
                if stype == "remote-outbound-rtp" and getattr(s, "kind", "") == "video":
                    sent = int(getattr(s, "bytesSent", 0) or 0)
                    if self._last_bytes:
                        payload["bitrate_kbps"] = round(
                            (sent - self._last_bytes) * 8 / dt / 1000, 1
                        )
                    self._last_bytes = sent
                elif stype == "inbound-rtp" and getattr(s, "kind", "") == "video":
                    payload["packets_lost"] = getattr(s, "packetsLost", 0)
                    # RTCP jitter comes in media-clock units (90 kHz for video)
                    jitter_units = getattr(s, "jitter", 0) or 0
                    payload["jitter_ms"] = round(jitter_units / 90.0, 1)
        except Exception:  # noqa: BLE001
            log.debug("getStats failed", exc_info=True)

        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:  # noqa: BLE001
            pass

        log.info(
            "[%s] fps: %5.1f | frames: %d | %s",
            self.client_addr,
            fps,
            self._frames,
            f"{payload.get('bitrate_kbps', '?')} kbps",
        )
