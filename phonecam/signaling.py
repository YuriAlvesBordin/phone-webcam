"""WebSocket signaling endpoint for WebRTC session setup.

Protocol (JSON text messages):
    phone -> server : {"type": "auth", "pin": "123456"}
    server -> phone : {"type": "auth_ok", "cfg": {...}}       (or close 4003)
    phone -> server : {"type": "offer", "sdp": "..."}         (initial + renegotiation)
    server -> phone : {"type": "answer", "sdp": "..."}
    both ways       : {"type": "candidate", "candidate": {...}}
    server -> phone : {"type": "stats", ...}                   (periodic)
    server -> phone : {"type": "bye", "reason": "..."}         (kicked)

The PIN travels in the first authenticated message - never in the URL.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from phonecam.config import AUTH_TIMEOUT_S, MAX_SIGNAL_MSG_BYTES
from phonecam.security import origin_allowed, pin_matches
from phonecam.webrtc import HAS_AIORTC, RTCSession

log = logging.getLogger(__name__)


class SessionManager:
    """Tracks the single active session; a new client kicks the previous one
    (video and audio share one PeerConnection, so this also fixes the old
    concurrent-audio-writer corruption)."""

    def __init__(self) -> None:
        self._current: RTCSession | None = None
        self._lock = asyncio.Lock()

    @property
    def current(self) -> RTCSession | None:
        return self._current

    async def replace(self, session: RTCSession) -> None:
        async with self._lock:
            old, self._current = self._current, session
        if old is not None and old is not session:
            log.info("Kicking previous client: %s", old.client_addr)
            await old.kick()

    async def clear(self, session: RTCSession) -> None:
        async with self._lock:
            if self._current is session:
                self._current = None


async def signal_endpoint(ws: WebSocket) -> None:
    cfg = ws.app.state.cfg
    pin = ws.app.state.pin
    limiter = ws.app.state.rate_limiter
    client_ip = ws.client.host if ws.client else "?"

    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        log.warning("Rejected signaling with bad Origin: %r", ws.headers.get("origin"))
        # Accept-then-close keeps semantics uniform across ASGI servers
        # (a pre-accept close is rendered as an opaque HTTP 403).
        await ws.accept()
        await ws.close(code=1008, reason="Bad Origin")
        return

    remaining = limiter.lockout_remaining(client_ip)
    if remaining > 0:
        await ws.accept()
        try:
            await ws.send_text(
                json.dumps(
                    {"type": "error", "code": "locked", "retry_after_s": round(remaining, 1)}
                )
            )
            await ws.close(code=4029, reason="Too many failed attempts")
        except Exception:  # noqa: BLE001
            pass
        log.warning("Rejected locked-out client %s (%.0fs remaining)", client_ip, remaining)
        return

    await ws.accept()

    # --- authentication: first message must carry the PIN ---
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect):
        return
    if len(raw) > MAX_SIGNAL_MSG_BYTES:
        await ws.close(code=1009, reason="Message too large")
        return
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        msg = None
    if (
        not isinstance(msg, dict)
        or msg.get("type") != "auth"
        or not pin_matches(str(msg.get("pin", "")), pin)
    ):
        limiter.record_failure(client_ip)
        log.warning("Auth failed from %s", client_ip)
        await ws.close(code=4003, reason="Invalid PIN")
        return
    limiter.reset(client_ip)

    if not HAS_AIORTC:
        await ws.close(code=4005, reason="Server missing aiortc (pip install aiortc)")
        return

    await ws.send_text(
        json.dumps(
            {
                "type": "auth_ok",
                "cfg": {
                    "width": cfg.width,
                    "height": cfg.height,
                    "fps": cfg.fps,
                    "bitrate_kbps": cfg.bitrate_kbps,
                    "audio": ws.app.state.audio_sink is not None,
                },
            }
        )
    )
    log.info("Client authenticated: %s", client_ip)

    session = RTCSession(ws, cfg, ws.app.state.vcam, ws.app.state.audio_sink)
    await ws.app.state.manager.replace(session)

    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_SIGNAL_MSG_BYTES:
                await ws.close(code=1009, reason="Message too large")
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "offer":
                answer_sdp = await session.handle_offer(msg["sdp"])
                await ws.send_text(json.dumps({"type": "answer", "sdp": answer_sdp}))
            elif mtype == "candidate":
                await session.add_candidate(msg.get("candidate") or {})
            elif mtype == "bye":
                break
            else:
                log.debug("Unknown signaling message type: %r", mtype)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("Signaling error: %s", e)
    finally:
        await ws.app.state.manager.clear(session)
        await session.close()
        log.info("Client disconnected: %s", client_ip)
