// PhoneCam app: connection lifecycle, zoom/gestures, mic, stats, reconnect.

import { CameraController } from "./camera.js";
import { SignalChannel, SignalError } from "./signaling.js";
import { PhoneCamSession } from "./webrtc.js";
import { WakeLockManager } from "./wakelock.js";
import {
  el,
  hidePlaceholder,
  hideReconnect,
  hideStats,
  pulseZoomIndicator,
  renderStats,
  setControlsEnabled,
  setPinError,
  setStatus,
  setTorchButtonVisible,
  showMainScreen,
  showPinScreen,
  showPlaceholder,
  showReconnect,
  updateZoomUi,
} from "./ui.js";

const ZOOM_MIN = 1.0;
const ZOOM_MAX = 4.0;
const ZOOM_STEP = 0.25;
const RECONNECT_DELAYS = [1000, 2000, 2000, 5000, 5000, 10000, 10000, 15000];

const state = {
  pin: null,
  cfg: null,
  signal: null,
  session: null,
  camera: null,
  connected: false,
  paused: false,
  userClosed: false,
  reconnectAttempts: 0,
  reconnectTimer: null,
  zoom: 1.0,
  panX: 0,
  panY: 0,
  micOn: false,
  torchOn: false,
  statsVisible: false,
  statsTimer: null,
  lastStatsBytes: 0,
  lastStatsTime: performance.now(),
};

const wakeLock = new WakeLockManager(el.wakeHint);
wakeLock.isStreaming = () => state.connected;
wakeLock.attach();

// ================================================================ connect

async function connect() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const isHttp = location.protocol === "http:";
    const isLocalhost = ["localhost", "127.0.0.1"].includes(location.hostname);
    if (isHttp && !isLocalhost) {
      setPinError(
        "Navegador bloqueou a câmera via HTTP. Use HTTPS: troque a URL para " +
          "https://" + location.host + "/ e aceite o certificado."
      );
    } else {
      setPinError("Este navegador não suporta getUserMedia. Tente Chrome, Safari ou Firefox atualizado.");
    }
    return;
  }

  const pin = el.pinInput.value.trim();
  if (!/^\d{6}$/.test(pin)) {
    setPinError("PIN deve ter 6 dígitos numéricos.");
    return;
  }
  setPinError("");
  state.pin = pin;
  state.userClosed = false;

  el.connectBtn.disabled = true;
  el.connectBtn.textContent = "Conectando…";

  try {
    await openSession(pin);
  } catch (e) {
    if (e instanceof SignalError) {
      showPinScreen(e.message);
    } else {
      showPinScreen("Erro: " + (e.message || e));
    }
  }
}

async function openSession(pin) {
  // --- signaling ---
  const signal = new SignalChannel();
  await signal.connect();
  const cfg = await signal.authenticate(pin);
  state.signal = signal;
  state.cfg = cfg;

  signal.onmessage = routeServerMessage;
  signal.onclose = handleSignalClose;

  // --- camera ---
  const camera = new CameraController();
  await camera.start({ width: cfg.width, height: cfg.height, fps: cfg.fps, videoEl: el.video });
  state.camera = camera;

  // --- webrtc ---
  const session = new PhoneCamSession(signal, cfg);
  state.session = session;
  session.pc.onconnectionstatechange = () => handleIceState(session.pc.connectionState);
  await session.start(camera.videoTrack);

  // --- UI ---
  state.connected = true;
  state.paused = false;
  state.reconnectAttempts = 0;
  hideReconnect();
  hidePlaceholder();
  showMainScreen();
  setControlsEnabled(true);
  setStatus("Conectado - transmitindo via WebRTC", "connected");
  resetZoom();

  setTorchButtonVisible(camera.capabilities.torch);
  updateTorchUi();

  const audioAvailable = !!cfg.audio;
  el.micToggleBtn.disabled = !audioAvailable;
  setMicUi(false, audioAvailable ? "Pronto p/ ativar" : "Servidor sem áudio");

  wakeLock.requested = true;
  await wakeLock.acquire();

  startStatsLoop();
}

function routeServerMessage(msg) {
  if (!state.session) return;
  switch (msg.type) {
    case "answer":
      state.session.handleAnswer(msg.sdp).catch((e) => setStatus("Erro na negociação: " + e.message, "error"));
      break;
    case "candidate":
      state.session.addCandidate(msg.candidate).catch(() => {});
      break;
    case "stats":
      el.fps.textContent = (msg.fps != null ? msg.fps.toFixed(1) : "-") + " fps";
      if (msg.bitrate_kbps != null) {
        el.rtt.textContent = (msg.bitrate_kbps / 1000).toFixed(1) + " Mb/s";
      }
      if (state.connected) el.liveDot.classList.add("live");
      break;
    case "bye":
      handleKicked(msg.reason || "Outro celular conectou no PC.");
      break;
  }
}

function handleSignalClose(ev) {
  if (state.userClosed) return;
  if (ev.code === 4003) {
    tearDown();
    showPinScreen("PIN inválido. Verifique no terminal do PC.");
    return;
  }
  scheduleReconnect();
}

function handleKicked(message) {
  state.userClosed = true;
  cancelReconnect();
  tearDown();
  showPinScreen(message + " Apenas um celular por vez.");
}

function handleIceState(pcState) {
  if (!state.session) return;
  if (pcState === "connected") {
    hideReconnect();
    el.liveDot.classList.add("live");
    setStatus(state.paused ? "Pausado" : "Transmitindo", state.paused ? "" : "connected");
  } else if (pcState === "disconnected") {
    showReconnect("Conexão instável - recuperando…");
    setStatus("Conexão instável", "error");
    el.liveDot.classList.remove("live");
  } else if (pcState === "failed") {
    if (!state.userClosed) scheduleReconnect();
  }
}

// ================================================================ teardown

function tearDown() {
  stopStatsLoop();
  if (state.reconnectTimer) cancelReconnect();
  if (state.session) {
    state.session.close().catch(() => {});
    state.session = null;
  }
  if (state.camera) {
    state.camera.stop();
    state.camera = null;
  }
  if (state.signal) {
    state.signal.close();
    state.signal = null;
  }
  state.connected = false;
  state.paused = false;
  state.micOn = false;
  state.torchOn = false;
  state.zoom = 1.0;
  state.panX = 0;
  state.panY = 0;
  el.liveDot.classList.remove("live");
  el.fps.textContent = "- fps";
  el.rtt.textContent = "";
  setControlsEnabled(false);
  setTorchButtonVisible(false);
  wakeLock.release();
}

// ================================================================ reconnect

function cancelReconnect() {
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function scheduleReconnect() {
  if (state.userClosed || state.reconnectTimer) return;

  const delayIdx = Math.min(state.reconnectAttempts, RECONNECT_DELAYS.length - 1);
  const delay = RECONNECT_DELAYS[delayIdx];
  state.reconnectAttempts++;

  showReconnect(`Reconectando em ${Math.round(delay / 1000)}s (tentativa ${state.reconnectAttempts})…`);
  setStatus(`Reconexão em ${Math.round(delay / 1000)}s`, "error");
  el.liveDot.classList.remove("live");

  state.reconnectTimer = setTimeout(async () => {
    state.reconnectTimer = null;
    if (state.userClosed) return;
    tearDown();
    try {
      await openSession(state.pin);
    } catch (e) {
      if (e instanceof SignalError && e.code === 4003) {
        showPinScreen(e.message);
        return;
      }
      scheduleReconnect();
    }
  }, delay);
}

// ================================================================ zoom

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function clampPan() {
  const maxPan = Math.max(0, (state.zoom - 1) / state.zoom);
  state.panX = clamp(state.panX, -maxPan, maxPan);
  state.panY = clamp(state.panY, -maxPan, maxPan);
}

function setZoom(z, { pulse = true } = {}) {
  const old = state.zoom;
  state.zoom = clamp(z, ZOOM_MIN, ZOOM_MAX);
  updateZoomUi(state.zoom);
  if (pulse && Math.abs(state.zoom - old) > 0.01) pulseZoomIndicator();

  if (state.zoom <= 1.001) {
    state.panX = 0;
    state.panY = 0;
  } else {
    clampPan();
  }

  if (!state.camera || !state.session) return;

  if (state.camera.capabilities.zoom) {
    state.camera.setNativeZoom(state.zoom).catch(() => {});
  } else if (state.zoom > 1.001) {
    state.session.startDigitalZoom(makeCropFn());
  } else {
    state.session.stopDigitalZoom().catch(() => {});
  }
}

function resetZoom() {
  setZoom(1.0, { pulse: false });
}

function zoomBy(delta) {
  setZoom(state.zoom + delta);
}

/** Crop function for the digital-zoom canvas pipeline (no distortion). */
function makeCropFn() {
  const W = state.cfg ? state.cfg.width : 1280;
  const H = state.cfg ? state.cfg.height : 720;
  const video = el.video;
  return (ctx) => {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!vw || !vh) return;

    const targetAspect = W / H;
    const videoAspect = vw / vh;
    let sw, sh;
    if (videoAspect > targetAspect) {
      sh = vh;
      sw = vh * targetAspect;
    } else {
      sw = vw;
      sh = vw / targetAspect;
    }
    let sx = (vw - sw) / 2;
    let sy = (vh - sh) / 2;

    const z = state.zoom;
    if (z > 1.001) {
      const newSw = sw / z;
      const newSh = sh / z;
      sx = sx + (sw - newSw) / 2 + (state.panX * (sw - newSw)) / 2;
      sy = sy + (sh - newSh) / 2 + (state.panY * (sh - newSh)) / 2;
      sw = newSw;
      sh = newSh;
    }

    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, W, H);
  };
}

// ================================================================ mic / torch

function setMicUi(on, statusText = "", statusCls = "") {
  el.micToggleBtn.textContent = on ? "🎤 Microfone ON" : "🎤 Microfone OFF";
  el.micToggleBtn.classList.toggle("on", on);
  if (statusText) {
    el.audioStatus.textContent = statusText;
    el.audioStatus.className = "audio-status" + (statusCls ? " " + statusCls : "");
  }
}

async function toggleMic() {
  if (!state.session || !state.cfg?.audio || state.micBusy) return;
  state.micBusy = true;
  el.micToggleBtn.disabled = true;
  try {
    if (!state.micOn) {
      await state.session.enableMic();
      state.micOn = true;
      setMicUi(true, "Transmitindo…", "active");
    } else {
      await state.session.disableMic();
      state.micOn = false;
      setMicUi(false, "Pronto p/ ativar");
    }
  } catch (e) {
    setMicUi(state.micOn, "Erro: " + e.message, "error");
  } finally {
    state.micBusy = false;
    el.micToggleBtn.disabled = !state.cfg?.audio;
  }
}

function updateTorchUi() {
  el.torchBtn.textContent = state.torchOn ? "🔦 Lanterna ON" : "🔦 Lanterna";
  el.torchBtn.disabled = !state.connected;
}

async function toggleTorch() {
  if (!state.camera) return;
  const ok = await state.camera.setTorch(!state.torchOn);
  if (ok) {
    state.torchOn = !state.torchOn;
    updateTorchUi();
  }
}

// ================================================================ camera controls

async function flipCamera() {
  if (!state.camera) return;
  el.flipBtn.disabled = true;
  resetZoom();
  try {
    const facing = state.camera.facingUser ? "environment" : "user";
    const track = await state.camera.start({
      width: state.cfg.width,
      height: state.cfg.height,
      fps: state.cfg.fps,
      facing,
      videoEl: el.video,
    });
    if (state.session) await state.session.replaceCamera(track);
    setTorchButtonVisible(state.camera.capabilities.torch);
    state.torchOn = false;
    updateTorchUi();
  } catch (e) {
    setStatus("Erro ao trocar câmera: " + e.message, "error");
  } finally {
    el.flipBtn.disabled = false;
  }
}

function togglePause() {
  if (!state.session) return;
  state.paused = !state.paused;
  state.session.setVideoEnabled(!state.paused);
  el.toggleBtn.textContent = state.paused ? "▶ Retomar" : "⏸ Pausar";
  setStatus(state.paused ? "Pausado" : "Transmitindo", state.paused ? "" : "connected");
  if (state.paused) {
    wakeLock.release();
  } else {
    wakeLock.acquire();
  }
}

// ================================================================ stats overlay

function startStatsLoop() {
  stopStatsLoop();
  state.lastStatsBytes = 0;
  state.lastStatsTime = performance.now();
  state.statsTimer = setInterval(renderStatsTick, 1000);
}

function stopStatsLoop() {
  if (state.statsTimer) {
    clearInterval(state.statsTimer);
    state.statsTimer = null;
  }
}

async function renderStatsTick() {
  if (!state.statsVisible || !state.session) return;
  const s = await state.session.collectStats();
  if (!s) return;

  const now = performance.now();
  const dt = (now - state.lastStatsTime) / 1000;
  let bitrate = "";
  if (s.bytes != null && dt > 0 && state.lastStatsBytes) {
    const mbps = ((s.bytes - state.lastStatsBytes) * 8) / dt / 1e6;
    bitrate = mbps >= 1 ? mbps.toFixed(1) + " Mb/s" : (mbps * 1000).toFixed(0) + " kb/s";
  }
  if (s.bytes != null) state.lastStatsBytes = s.bytes;
  state.lastStatsTime = now;

  const lines = [
    bitrate ? "↑ " + bitrate : "↑ -",
    (s.fps != null ? s.fps.toFixed(0) : "?") + " fps" + (s.res ? "  " + s.res : ""),
    s.rttMs != null ? "RTT " + s.rttMs + " ms" : "RTT -",
    s.packetsLost != null ? "perda " + s.packetsLost : "",
  ].filter(Boolean);
  renderStats(lines.join("\n"));
}

// ================================================================ gestures

const gestures = { pinchDist: 0, pinchZoom: 1, dragging: false, sx: 0, sy: 0, spanX: 0, spanY: 0, tapT: 0, tapMoved: false };

const touchDist = (t1, t2) => Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);

el.video.addEventListener("touchstart", (e) => {
  if (e.touches.length === 2) {
    gestures.pinchDist = touchDist(e.touches[0], e.touches[1]);
    gestures.pinchZoom = state.zoom;
    gestures.dragging = false;
    e.preventDefault();
  } else if (e.touches.length === 1 && state.zoom > 1.001) {
    gestures.sx = e.touches[0].clientX;
    gestures.sy = e.touches[0].clientY;
    gestures.spanX = state.panX;
    gestures.spanY = state.panY;
    gestures.dragging = true;
  }
  gestures.tapT = Date.now();
  gestures.tapMoved = false;
}, { passive: false });

el.video.addEventListener("touchmove", (e) => {
  gestures.tapMoved = true;
  if (e.touches.length === 2 && gestures.pinchDist > 0) {
    const ratio = touchDist(e.touches[0], e.touches[1]) / gestures.pinchDist;
    setZoom(gestures.pinchZoom * ratio);
    e.preventDefault();
  } else if (e.touches.length === 1 && gestures.dragging) {
    const dx = e.touches[0].clientX - gestures.sx;
    const dy = e.touches[0].clientY - gestures.sy;
    const rect = el.video.getBoundingClientRect();
    const scale = 2 * ((state.zoom - 1) / state.zoom);
    state.panX = gestures.spanX - (dx / rect.width) * scale;
    state.panY = gestures.spanY - (dy / rect.height) * scale;
    clampPan();
    if (state.session && !state.camera.capabilities.zoom && state.zoom > 1.001) {
      state.session.startDigitalZoom(makeCropFn());
    }
    e.preventDefault();
  }
}, { passive: false });

el.video.addEventListener("touchend", (e) => {
  if (e.touches.length < 2) gestures.pinchDist = 0;
  if (e.touches.length === 0) gestures.dragging = false;
  // single tap (no drag, quick) toggles the stats overlay
  if (!gestures.tapMoved && Date.now() - gestures.tapT < 300 && e.changedTouches.length === 1) {
    state.statsVisible = !state.statsVisible;
    if (state.statsVisible) renderStatsTick();
    else hideStats();
  }
}, { passive: false });

el.video.addEventListener("wheel", (e) => {
  if (!state.connected) return;
  e.preventDefault();
  setZoom(state.zoom - Math.sign(e.deltaY) * 0.2);
}, { passive: false });

// ================================================================ keyboard (desktop)

document.addEventListener("keydown", (e) => {
  if (!state.connected) return;
  if (e.target === el.pinInput) return;
  switch (e.key) {
    case " ": e.preventDefault(); togglePause(); break;
    case "z": case "Z": zoomBy(-ZOOM_STEP); break;
    case "x": case "X": zoomBy(ZOOM_STEP); break;
    case "m": case "M": toggleMic(); break;
    case "f": case "F": flipCamera(); break;
    case "t": case "T": if (!el.torchBtn.classList.contains("hidden")) toggleTorch(); break;
  }
});

// ================================================================ orientation hint

let orientTimer = null;
el.video.addEventListener("resize", () => {
  const portrait = el.video.videoHeight > el.video.videoWidth;
  if (portrait && el.video.videoWidth) {
    el.orientHint.classList.add("show");
    clearTimeout(orientTimer);
    orientTimer = setTimeout(() => el.orientHint.classList.remove("show"), 3000);
  } else {
    el.orientHint.classList.remove("show");
  }
});

// ================================================================ events

el.pinInput.addEventListener("input", (e) => {
  e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
});
el.pinInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") connect();
});
el.connectBtn.addEventListener("click", connect);
el.flipBtn.addEventListener("click", flipCamera);
el.toggleBtn.addEventListener("click", togglePause);
el.torchBtn.addEventListener("click", toggleTorch);
el.zoomOutBtn.addEventListener("click", () => zoomBy(-ZOOM_STEP));
el.zoomInBtn.addEventListener("click", () => zoomBy(ZOOM_STEP));
el.zoomSlider.addEventListener("input", (e) => setZoom(parseFloat(e.target.value), { pulse: false }));
el.micToggleBtn.addEventListener("click", toggleMic);
document.querySelectorAll(".zoom-quick-btn").forEach((btn) => {
  btn.addEventListener("click", () => setZoom(parseFloat(btn.dataset.zoom)));
});

window.addEventListener("beforeunload", () => {
  state.userClosed = true;
  cancelReconnect();
  stopStatsLoop();
  if (state.session) state.session.close().catch(() => {});
  if (state.camera) state.camera.stop();
  if (state.signal) state.signal.close();
  wakeLock.release();
});

// ================================================================ auto-connect (?pin=)

(function autoConnectFromUrl() {
  const urlPin = new URLSearchParams(window.location.search).get("pin");
  if (urlPin && /^\d{6}$/.test(urlPin)) {
    el.pinInput.value = urlPin;
    setTimeout(connect, 300);
  } else {
    el.pinInput.focus();
  }
})();
