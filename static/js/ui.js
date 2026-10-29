// Acesso ao DOM e estado visual. O resto do app não usa document.getElementById.

const $ = (id) => document.getElementById(id);

export const el = Object.freeze({
  screens: { connect: $("screenConnect"), connecting: $("screenConnecting"), live: $("screenLive"), ended: $("screenEnded") },
  pinInput: $("pinInput"),
  pinError: $("pinError"),
  connectBtn: $("connectBtn"),
  connectingTitle: $("connectingTitle"),
  steps: document.querySelectorAll("#steps li"),
  video: $("video"),
  statusPill: $("statusPill"),
  liveDot: $("liveDot"),
  statusText: $("statusText"),
  statusMeta: $("statusMeta"),
  statsPanel: $("statsPanel"),
  toast: $("toast"),
  zoomIndicator: $("zoomIndicator"),
  wakeHint: $("wakeHint"),
  controls: $("controls"),
  zoomChips: document.querySelectorAll("#zoomChips button"),
  flipBtn: $("flipBtn"),
  micBtn: $("micBtn"),
  micLabel: $("micLabel"),
  pauseBtn: $("pauseBtn"),
  pauseIcon: $("pauseIcon"),
  pauseLabel: $("pauseLabel"),
  torchBtn: $("torchBtn"),
  endedTitle: $("endedTitle"),
  endedMsg: $("endedMsg"),
  endedBtn: $("endedBtn"),
  endedPinBtn: $("endedPinBtn"),
});

// ---------- telas ----------

export function showScreen(name) {
  for (const [key, node] of Object.entries(el.screens)) node.classList.toggle("hidden", key !== name);
  if (name !== "live") {
    el.video.srcObject = null;
    el.video.classList.remove("mirror");
    hideStats();
    hideToast();
  }
}

export function showConnect(message = "") {
  setPinError(message);
  el.pinInput.value = "";
  updateConnectButton();
  showScreen("connect");
}

export function showConnecting(title = "Conectando…") {
  el.connectingTitle.textContent = title;
  setStep(0);
  showScreen("connecting");
}

/** Etapa atual (0..2): as anteriores ficam marcadas como feitas. */
export function setStep(index) {
  el.steps.forEach((li, i) => {
    li.classList.toggle("done", i < index);
    li.classList.toggle("active", i === index);
  });
}

export function showEnded(title, message, { retryLabel = "Tentar de novo", canEnterPin = true } = {}) {
  el.endedTitle.textContent = title;
  el.endedMsg.textContent = message;
  el.endedBtn.textContent = retryLabel;
  el.endedPinBtn.classList.toggle("hidden", !canEnterPin);
  showScreen("ended");
}

// ---------- PIN ----------

export function setPinError(msg) {
  el.pinError.textContent = msg || "";
  if (msg) {
    el.pinInput.classList.remove("bad");
    void el.pinInput.offsetWidth; // reinicia a animação
    el.pinInput.classList.add("bad");
  } else {
    el.pinInput.classList.remove("bad");
  }
}

export function updateConnectButton() {
  el.connectBtn.disabled = !/^\d{6}$/.test(el.pinInput.value);
}

// ---------- barra de estado ----------

/** kind: "wait" | "live" | "paused" | "bad" */
export function setStatus(text, kind = "wait", meta = "") {
  el.statusText.textContent = text;
  el.statusMeta.textContent = meta;
  el.liveDot.className = "dot" + (kind === "wait" ? "" : " " + kind);
}

let toastTimer = null;
export function toast(message, { bad = false, ms = 3000 } = {}) {
  el.toast.textContent = message;
  el.toast.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  if (ms > 0) toastTimer = setTimeout(hideToast, ms);
}

export function hideToast() {
  clearTimeout(toastTimer);
  el.toast.classList.add("hidden");
}

// ---------- controles que somem sozinhos ----------

let awakeTimer = null;
let pinned = false;

/** Mantém os controles visíveis (pausado, reconectando). */
export function pinControls(on) {
  pinned = on;
  wakeControls();
}

export function wakeControls() {
  const live = el.screens.live;
  live.classList.add("awake");
  clearTimeout(awakeTimer);
  if (!pinned) awakeTimer = setTimeout(() => live.classList.remove("awake"), 4500);
}

export function toggleControls() {
  const live = el.screens.live;
  if (live.classList.contains("awake") && !pinned) {
    clearTimeout(awakeTimer);
    live.classList.remove("awake");
  } else {
    wakeControls();
  }
}

// ---------- botões ----------

export function setControlsEnabled(enabled) {
  for (const b of [el.flipBtn, el.pauseBtn, ...el.zoomChips]) b.disabled = !enabled;
  if (!enabled) el.micBtn.disabled = true;
}

export function setTorchButtonVisible(visible) {
  el.torchBtn.classList.toggle("hidden", !visible);
}

export function setTorchUi(on) {
  el.torchBtn.classList.toggle("alert", on);
}

export function setMicUi({ on, available = true, busy = false }) {
  el.micBtn.disabled = !available || busy;
  el.micBtn.classList.toggle("on", on);
  el.micLabel.textContent = !available ? "Sem áudio" : on ? "Mic" : "Mudo";
}

export function setPauseUi(paused) {
  el.pauseIcon.setAttribute("href", paused ? "#i-play" : "#i-pause");
  el.pauseLabel.textContent = paused ? "Retomar" : "Pausar";
  el.pauseBtn.classList.toggle("alert", paused);
}

// ---------- zoom ----------

let zoomTimer = null;
export function updateZoomUi(zoom) {
  el.zoomChips.forEach((b) => b.classList.toggle("active", Math.abs(parseFloat(b.dataset.zoom) - zoom) < 0.05));
  el.zoomIndicator.textContent = zoom.toFixed(1) + "×";
}

export function pulseZoomIndicator() {
  el.zoomIndicator.classList.add("show");
  clearTimeout(zoomTimer);
  zoomTimer = setTimeout(() => el.zoomIndicator.classList.remove("show"), 900);
}

// ---------- estatísticas ----------

export function renderStats(text) {
  el.statsPanel.textContent = text;
  el.statsPanel.classList.remove("hidden");
}

export function hideStats() {
  el.statsPanel.classList.add("hidden");
}
