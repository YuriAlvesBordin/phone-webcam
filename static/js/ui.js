// Central DOM access and UI state helpers.
// All element lookups live here so the rest of the app never touches
// document.getElementById directly.

const $ = (id) => document.getElementById(id);

export const el = Object.freeze({
  pinOverlay: $("pinOverlay"),
  pinInput: $("pinInput"),
  pinError: $("pinError"),
  connectBtn: $("connectBtn"),
  topbar: $("topbar"),
  stage: $("stage"),
  video: $("video"),
  placeholder: $("placeholder"),
  controls: $("controls"),
  flipBtn: $("flipBtn"),
  toggleBtn: $("toggleBtn"),
  torchBtn: $("torchBtn"),
  statusBar: $("statusBar"),
  liveDot: $("liveDot"),
  fps: $("fps"),
  rtt: $("rtt"),
  orientHint: $("orientHint"),
  zoomSlider: $("zoomSlider"),
  zoomOutBtn: $("zoomOutBtn"),
  zoomInBtn: $("zoomInBtn"),
  zoomLabel: $("zoomLabel"),
  wakeHint: $("wakeHint"),
  micToggleBtn: $("micToggleBtn"),
  audioStatus: $("audioStatus"),
  zoomIndicator: $("zoomIndicator"),
  reconnectBar: $("reconnectBar"),
  statsPanel: $("statsPanel"),
});

export function setStatus(msg, cls = "") {
  el.statusBar.textContent = msg;
  el.statusBar.className = "status-bar " + cls;
}

export function setPinError(msg) {
  el.pinError.textContent = msg || "";
}

export function showMainScreen() {
  el.pinOverlay.classList.add("hidden");
  el.topbar.classList.remove("hidden");
  el.stage.classList.remove("hidden");
  el.controls.classList.remove("hidden");
  el.statusBar.classList.remove("hidden");
}

export function showPinScreen(message = "") {
  el.pinError.textContent = message;
  el.pinOverlay.classList.remove("hidden");
  el.topbar.classList.add("hidden");
  el.stage.classList.add("hidden");
  el.controls.classList.add("hidden");
  el.statusBar.classList.add("hidden");
  el.connectBtn.disabled = false;
  el.connectBtn.textContent = "Conectar";
  el.video.srcObject = null;
  el.video.classList.remove("mirror");
  el.liveDot.classList.remove("live");
  hideReconnect();
  hideStats();
}

export function setControlsEnabled(enabled) {
  for (const btn of [el.flipBtn, el.toggleBtn, el.zoomOutBtn, el.zoomInBtn]) {
    btn.disabled = !enabled;
  }
  el.zoomSlider.disabled = !enabled;
  document.querySelectorAll(".zoom-quick-btn").forEach((b) => (b.disabled = !enabled));
}

export function setTorchButtonVisible(visible) {
  el.torchBtn.classList.toggle("hidden", !visible);
  if (!visible) el.torchBtn.disabled = true;
}

const PLACEHOLDER_DEFAULT = `<div class="icon" aria-hidden="true">📷</div><div>Aguardando câmera…</div>`;

export function showPlaceholder(html = PLACEHOLDER_DEFAULT) {
  el.placeholder.innerHTML = html;
  el.placeholder.style.display = "flex";
}

export function hidePlaceholder() {
  el.placeholder.style.display = "none";
}

export function showReconnect(text) {
  el.reconnectBar.textContent = text;
  el.reconnectBar.classList.remove("hidden");
}

export function hideReconnect() {
  el.reconnectBar.classList.add("hidden");
}

// ---------- zoom ----------

export function updateZoomUi(zoom) {
  el.zoomSlider.value = String(zoom);
  el.zoomLabel.textContent = zoom.toFixed(1) + "×";
  el.zoomIndicator.textContent = zoom.toFixed(1) + "×";

  document.querySelectorAll(".zoom-quick-btn").forEach((btn) => {
    const btnZoom = parseFloat(btn.dataset.zoom);
    btn.classList.toggle("active", Math.abs(btnZoom - zoom) < 0.05);
  });

  if (zoom > 1.001) {
    el.zoomIndicator.classList.remove("hidden");
    el.zoomIndicator.classList.add("show");
  } else {
    el.zoomIndicator.classList.remove("show");
  }
}

export function pulseZoomIndicator() {
  el.zoomIndicator.classList.add("changed");
  setTimeout(() => el.zoomIndicator.classList.remove("changed"), 300);
}

export function resetZoomUi() {
  updateZoomUi(1.0);
}

// ---------- stats overlay ----------

export function renderStats(text) {
  el.statsPanel.textContent = text;
  el.statsPanel.classList.remove("hidden");
}

export function hideStats() {
  el.statsPanel.classList.add("hidden");
}
