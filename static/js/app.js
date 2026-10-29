// PhoneCam: conexão, zoom/gestos, microfone, estatísticas e reconexão.
//
// Fluxo normal: o celular abre a URL do QR (`#k=<token>`), conecta sozinho e mostra o
// vídeo. Sem token (URL digitada), pede o PIN, e conecta ao completar 6 dígitos.

import { CameraController, CameraError } from "./camera.js?v=3";
import { SignalChannel, SignalError } from "./signaling.js?v=3";
import { PhoneCamSession } from "./webrtc.js?v=3";
import { WakeLockManager } from "./wakelock.js?v=3";
import {
  el,
  hideStats,
  pinControls,
  pulseZoomIndicator,
  renderStats,
  setControlsEnabled,
  setMicUi,
  setPauseUi,
  setPinError,
  setStatus,
  setStep,
  setTorchButtonVisible,
  setTorchUi,
  showConnect,
  showConnecting,
  showEnded,
  showScreen,
  toast,
  toggleControls,
  updateConnectButton,
  updateZoomUi,
  wakeControls,
} from "./ui.js?v=3";

const ZOOM_MIN = 1.0;
const ZOOM_MAX = 4.0;
const ZOOM_STEP = 0.25;
const RECONNECT_DELAYS = [1000, 2000, 2000, 5000, 5000, 10000, 10000, 15000];
const CRED_KEY = "phonecam.cred";
const MIC_KEY = "phonecam.mic";
const FACING_KEY = "phonecam.facing";

const state = {
  cred: null,
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
  micBusy: false,
  torchOn: false,
  statsVisible: false,
  statsTimer: null,
  lastStatsBytes: 0,
  lastStatsTime: performance.now(),
  serverMeta: "",
};

// ================================================================ armazenamento

const store = {
  get(area, key) {
    try {
      return window[area].getItem(key);
    } catch {
      return null;
    }
  },
  set(area, key, value) {
    try {
      if (value == null) window[area].removeItem(key);
      else window[area].setItem(key, value);
    } catch {
      /* modo privado: tudo certo, só não lembra */
    }
  },
};

function loadCred() {
  try {
    const c = JSON.parse(store.get("sessionStorage", CRED_KEY) || "null");
    if (c && (/^\d{6}$/.test(c.pin || "") || /^[0-9a-f]{32}$/.test(c.token || ""))) return c;
  } catch {
    /* ignora */
  }
  return null;
}

const saveCred = (cred) => store.set("sessionStorage", CRED_KEY, cred ? JSON.stringify(cred) : null);

const wakeLock = new WakeLockManager(el.wakeHint);
wakeLock.isStreaming = () => state.connected;
wakeLock.attach();

// ================================================================ conectar

function unsupportedReason() {
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) return null;
  if (location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(location.hostname)) {
    return "O navegador só libera a câmera em páginas seguras. Abra https://" + location.host + "/ e aceite o aviso do certificado.";
  }
  return "Este navegador não permite usar a câmera. Tente o Chrome ou o Safari atualizado.";
}

/** Ponto de entrada de toda conexão iniciada pelo usuário (QR, PIN ou "tentar de novo"). */
async function start(cred) {
  const problem = unsupportedReason();
  if (problem) {
    showEnded("Câmera indisponível", problem, { retryLabel: "Tentar de novo", canEnterPin: false });
    return;
  }
  state.cred = cred;
  state.userClosed = false;
  state.reconnectAttempts = 0;
  showConnecting();
  try {
    await openSession(cred);
    saveCred(cred);
  } catch (e) {
    tearDown();
    handleStartError(e, cred);
  }
}

function handleStartError(e, cred) {
  if (e instanceof SignalError && e.code === 4003) {
    saveCred(null);
    state.cred = null;
    showConnect(cred.token ? "O QR code expirou. Escaneie de novo ou digite o PIN." : "PIN incorreto. Confira na tela do PC.");
  } else if (e instanceof SignalError && e.code === 4029) {
    showEnded("Aguarde um pouco", e.message, { retryLabel: "Tentar de novo" });
  } else if (e instanceof SignalError) {
    showEnded("Sem conexão com o PC", e.message, { retryLabel: "Tentar de novo" });
  } else if (e instanceof CameraError) {
    showEnded("Câmera indisponível", e.message, { retryLabel: "Tentar de novo", canEnterPin: false });
  } else {
    showEnded("Algo deu errado", String((e && e.message) || e), { retryLabel: "Tentar de novo" });
  }
}

async function openSession(cred) {
  setStep(0);
  const signal = new SignalChannel();
  await signal.connect();
  const cfg = await signal.authenticate(cred);
  state.signal = signal;
  state.cfg = cfg;
  signal.onmessage = routeServerMessage;
  signal.onclose = handleSignalClose;

  setStep(1);
  const camera = new CameraController();
  const facing = store.get("localStorage", FACING_KEY) === "user" ? "user" : "environment";
  await camera.start({ width: cfg.width, height: cfg.height, fps: cfg.fps, facing, videoEl: el.video });
  state.camera = camera;

  setStep(2);
  const session = new PhoneCamSession(signal, cfg);
  state.session = session;
  session.pc.onconnectionstatechange = () => handleIceState(session.pc.connectionState);
  await session.start(camera.videoTrack);

  state.connected = true;
  state.paused = false;
  state.reconnectAttempts = 0;
  showScreen("live");
  setStatus("Conectando…", "wait");
  setControlsEnabled(true);
  setPauseUi(false);
  pinControls(false);
  wakeControls();
  resetZoom();
  setTorchButtonVisible(camera.capabilities.torch);
  state.torchOn = false;
  setTorchUi(false);

  const audioAvailable = !!cfg.audio;
  setMicUi({ on: false, available: audioAvailable });
  wakeLock.requested = true;
  await wakeLock.acquire();
  startStatsLoop();

  // Microfone ligado por padrão (quem usa como webcam quer o som), salvo se o usuário o desligou antes.
  if (audioAvailable && store.get("localStorage", MIC_KEY) !== "off") {
    setMic(true, { silent: true });
  }
}

function routeServerMessage(msg) {
  if (!state.session) return;
  switch (msg.type) {
    case "answer":
      state.session.handleAnswer(msg.sdp).catch((e) => toast("Erro na negociação: " + e.message, { bad: true }));
      break;
    case "candidate":
      state.session.addCandidate(msg.candidate).catch(() => {});
      break;
    case "stats":
      state.serverMeta = formatMeta(msg.fps, msg.bitrate_kbps);
      if (state.connected && !state.paused && !state.reconnectTimer) setStatus("Ao vivo", "live", state.serverMeta);
      break;
    case "bye":
      handleKicked(msg.reason);
      break;
  }
}

function formatMeta(fps, kbps) {
  const parts = [];
  if (fps != null) parts.push(Math.round(fps) + " fps");
  if (kbps != null) parts.push(kbps >= 1000 ? (kbps / 1000).toFixed(1) + " Mb/s" : Math.round(kbps) + " kb/s");
  return parts.join(" · ");
}

function handleSignalClose(ev) {
  if (state.userClosed) return;
  if (ev.code === 4003) {
    tearDown();
    saveCred(null);
    showConnect("O código mudou no PC. Escaneie o QR de novo ou digite o novo PIN.");
    return;
  }
  scheduleReconnect();
}

function handleKicked() {
  state.userClosed = true;
  cancelReconnect();
  tearDown();
  saveCred(null);
  showEnded("Outro celular conectou", "O PC aceita um celular por vez. Toque para assumir de novo.", {
    retryLabel: "Conectar este celular",
    canEnterPin: false,
  });
  state.cred = loadCred() || state.cred;
}

function handleIceState(pcState) {
  if (!state.session) return;
  if (pcState === "connected") {
    state.reconnectAttempts = 0;
    pinControls(state.paused);
    setStatus(state.paused ? "Pausado" : "Ao vivo", state.paused ? "paused" : "live", state.serverMeta);
    if (navigator.vibrate) navigator.vibrate(40);
    toast("Conectado ao PC", { ms: 1800 });
  } else if (pcState === "disconnected") {
    setStatus("Conexão instável", "bad");
    pinControls(true);
  } else if (pcState === "failed") {
    if (!state.userClosed) scheduleReconnect();
  }
}

// ================================================================ encerrar

function tearDown() {
  stopStatsLoop();
  cancelReconnect();
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
  state.serverMeta = "";
  setControlsEnabled(false);
  setTorchButtonVisible(false);
  wakeLock.release();
}

// ================================================================ reconexão

function cancelReconnect() {
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function scheduleReconnect() {
  if (state.userClosed || state.reconnectTimer || !state.cred) return;
  const delay = RECONNECT_DELAYS[Math.min(state.reconnectAttempts, RECONNECT_DELAYS.length - 1)];
  state.reconnectAttempts++;
  setStatus("Reconectando…", "bad", `tentativa ${state.reconnectAttempts}`);
  pinControls(true);

  state.reconnectTimer = setTimeout(async () => {
    state.reconnectTimer = null;
    if (state.userClosed) return;
    tearDown();
    try {
      await openSession(state.cred);
    } catch (e) {
      tearDown();
      if (e instanceof SignalError && e.code === 4003) {
        saveCred(null);
        showConnect("O código mudou no PC. Escaneie o QR de novo ou digite o novo PIN.");
      } else if (e instanceof CameraError) {
        handleStartError(e, state.cred);
      } else {
        scheduleReconnect();
      }
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

const resetZoom = () => setZoom(1.0, { pulse: false });
const zoomBy = (delta) => setZoom(state.zoom + delta);

/** Recorte do zoom digital (canvas), sem distorcer. */
function makeCropFn() {
  const W = state.cfg ? state.cfg.width : 1280;
  const H = state.cfg ? state.cfg.height : 720;
  const video = el.video;
  return (ctx) => {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!vw || !vh) return;
    const targetAspect = W / H;
    let sw, sh;
    if (vw / vh > targetAspect) {
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

// ================================================================ microfone / lanterna

async function setMic(on, { silent = false } = {}) {
  if (!state.session || !state.cfg?.audio || state.micBusy || state.micOn === on) return;
  state.micBusy = true;
  setMicUi({ on: state.micOn, busy: true });
  try {
    if (on) await state.session.enableMic();
    else await state.session.disableMic();
    state.micOn = on;
    store.set("localStorage", MIC_KEY, on ? "on" : "off");
    if (!silent) toast(on ? "Microfone ligado" : "Microfone no mudo", { ms: 1500 });
  } catch (e) {
    const denied = e && (e.name === "NotAllowedError" || e.name === "SecurityError");
    toast(denied ? "Microfone bloqueado. Libere nas configurações do navegador." : "Não consegui ligar o microfone.", { bad: true });
  } finally {
    state.micBusy = false;
    setMicUi({ on: state.micOn, available: !!state.cfg?.audio });
  }
}

const toggleMic = () => setMic(!state.micOn);

async function toggleTorch() {
  if (!state.camera) return;
  if (await state.camera.setTorch(!state.torchOn)) {
    state.torchOn = !state.torchOn;
    setTorchUi(state.torchOn);
  }
}

// ================================================================ câmera / pausa

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
    store.set("localStorage", FACING_KEY, facing);
    if (state.session) await state.session.replaceCamera(track);
    setTorchButtonVisible(state.camera.capabilities.torch);
    state.torchOn = false;
    setTorchUi(false);
  } catch (e) {
    toast(e instanceof CameraError ? e.message : "Não consegui trocar de câmera.", { bad: true });
  } finally {
    el.flipBtn.disabled = false;
  }
}

function togglePause() {
  if (!state.session) return;
  state.paused = !state.paused;
  state.session.setVideoEnabled(!state.paused);
  setPauseUi(state.paused);
  setStatus(state.paused ? "Pausado" : "Ao vivo", state.paused ? "paused" : "live", state.paused ? "" : state.serverMeta);
  pinControls(state.paused);
  if (state.paused) wakeLock.release();
  else wakeLock.acquire();
}

// ================================================================ estatísticas

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
  renderStats(
    [
      "envio  " + (bitrate || "-"),
      "vídeo  " + (s.fps != null ? s.fps.toFixed(0) : "?") + " fps" + (s.res ? "  " + s.res : ""),
      "atraso " + (s.rttMs != null ? s.rttMs + " ms" : "-"),
      "perdas " + (s.packetsLost != null ? s.packetsLost : "-"),
    ].join("\n")
  );
}

function toggleStats() {
  state.statsVisible = !state.statsVisible;
  if (state.statsVisible) renderStatsTick();
  else hideStats();
  wakeControls();
}

// ================================================================ gestos

const gestures = { pinchDist: 0, pinchZoom: 1, dragging: false, sx: 0, sy: 0, spanX: 0, spanY: 0, tapT: 0, tapMoved: false };
const touchDist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);

el.video.addEventListener(
  "touchstart",
  (e) => {
    if (e.touches.length === 2) {
      gestures.pinchDist = touchDist(e.touches[0], e.touches[1]);
      gestures.pinchZoom = state.zoom;
      gestures.dragging = false;
      e.preventDefault();
    } else if (e.touches.length === 1 && state.zoom > 1.001) {
      Object.assign(gestures, { sx: e.touches[0].clientX, sy: e.touches[0].clientY, spanX: state.panX, spanY: state.panY, dragging: true });
    }
    gestures.tapT = Date.now();
    gestures.tapMoved = false;
  },
  { passive: false }
);

el.video.addEventListener(
  "touchmove",
  (e) => {
    gestures.tapMoved = true;
    if (e.touches.length === 2 && gestures.pinchDist > 0) {
      setZoom(gestures.pinchZoom * (touchDist(e.touches[0], e.touches[1]) / gestures.pinchDist));
      e.preventDefault();
    } else if (e.touches.length === 1 && gestures.dragging) {
      const rect = el.video.getBoundingClientRect();
      const scale = 2 * ((state.zoom - 1) / state.zoom);
      state.panX = gestures.spanX - ((e.touches[0].clientX - gestures.sx) / rect.width) * scale;
      state.panY = gestures.spanY - ((e.touches[0].clientY - gestures.sy) / rect.height) * scale;
      clampPan();
      if (state.session && !state.camera.capabilities.zoom && state.zoom > 1.001) {
        state.session.startDigitalZoom(makeCropFn());
      }
      e.preventDefault();
    }
  },
  { passive: false }
);

el.video.addEventListener(
  "touchend",
  (e) => {
    if (e.touches.length < 2) gestures.pinchDist = 0;
    if (e.touches.length === 0) gestures.dragging = false;
    // toque rápido e parado: mostra/esconde os controles
    if (!gestures.tapMoved && Date.now() - gestures.tapT < 300 && e.changedTouches.length === 1) {
      toggleControls();
    }
  },
  { passive: false }
);

el.video.addEventListener("click", (e) => {
  if (e.pointerType === "mouse" || !("ontouchstart" in window)) toggleControls();
});

el.video.addEventListener(
  "wheel",
  (e) => {
    if (!state.connected) return;
    e.preventDefault();
    setZoom(state.zoom - Math.sign(e.deltaY) * 0.2);
  },
  { passive: false }
);

// ================================================================ teclado (desktop)

document.addEventListener("keydown", (e) => {
  if (!state.connected || e.target === el.pinInput) return;
  wakeControls();
  switch (e.key) {
    case " ": e.preventDefault(); togglePause(); break;
    case "z": case "Z": zoomBy(-ZOOM_STEP); break;
    case "x": case "X": zoomBy(ZOOM_STEP); break;
    case "m": case "M": toggleMic(); break;
    case "f": case "F": flipCamera(); break;
    case "t": case "T": if (!el.torchBtn.classList.contains("hidden")) toggleTorch(); break;
  }
});

// ================================================================ dica de orientação

el.video.addEventListener("resize", () => {
  if (el.video.videoWidth && el.video.videoHeight > el.video.videoWidth) {
    toast("Vire o celular para a horizontal: a imagem fica maior no PC", { ms: 3500 });
  }
});

// ================================================================ eventos

function submitPin() {
  const pin = el.pinInput.value.trim();
  if (!/^\d{6}$/.test(pin)) return;
  el.pinInput.blur();
  start({ pin });
}

el.pinInput.addEventListener("input", (e) => {
  e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
  setPinError("");
  updateConnectButton();
  if (e.target.value.length === 6) setTimeout(submitPin, 180); // conecta ao completar o PIN
});
el.pinInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitPin();
});
el.connectBtn.addEventListener("click", submitPin);
el.endedBtn.addEventListener("click", () => (state.cred || loadCred() ? start(state.cred || loadCred()) : showConnect()));
el.endedPinBtn.addEventListener("click", () => showConnect());
el.statusPill.addEventListener("click", toggleStats);
el.flipBtn.addEventListener("click", () => { wakeControls(); flipCamera(); });
el.pauseBtn.addEventListener("click", () => { wakeControls(); togglePause(); });
el.torchBtn.addEventListener("click", () => { wakeControls(); toggleTorch(); });
el.micBtn.addEventListener("click", () => { wakeControls(); toggleMic(); });
el.zoomChips.forEach((b) =>
  b.addEventListener("click", () => {
    wakeControls();
    setZoom(parseFloat(b.dataset.zoom));
  })
);

function shutdown() {
  state.userClosed = true;
  cancelReconnect();
  stopStatsLoop();
  if (state.session) state.session.close().catch(() => {});
  if (state.camera) state.camera.stop();
  if (state.signal) state.signal.close();
  wakeLock.release();
}
window.addEventListener("pagehide", shutdown);

// Voltou ao app depois de sair: o celular pode ter derrubado a conexão e a câmera.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || state.userClosed || !state.cred) return;
  const pc = state.session && state.session.pc.connectionState;
  if (!el.screens.live.classList.contains("hidden") && pc && pc !== "connected" && pc !== "connecting") {
    scheduleReconnect();
  }
});

// ================================================================ início

(function boot() {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get("k");
  // Tira a credencial da barra de endereço e do histórico.
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);

  const cred = token && /^[0-9a-f]{32}$/.test(token) ? { token } : loadCred();
  if (cred) start(cred);
  else {
    showConnect();
    if (!unsupportedReason()) el.pinInput.focus();
  }
})();
