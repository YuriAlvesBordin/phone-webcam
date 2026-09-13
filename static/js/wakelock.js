// Screen Wake Lock: keeps the phone screen on while streaming.

export class WakeLockManager {
  constructor(hintEl) {
    this.hintEl = hintEl;
    this.lock = null;
    this.requested = false; // user authorized keeping the screen on
    /** @type {() => boolean} returns true while a stream is active */
    this.isStreaming = () => false;
  }

  get supported() {
    return "wakeLock" in navigator;
  }

  async acquire() {
    if (!this.supported) {
      this._showHint();
      return;
    }
    if (this.lock && this.lock.type === "screen") {
      this._hideHint();
      return;
    }
    try {
      this.lock = await navigator.wakeLock.request("screen");
      this._hideHint();
      this.lock.addEventListener("release", () => {
        this.lock = null;
        if (this.requested && this.isStreaming()) this._showHint();
      });
    } catch {
      this._hideHint();
    }
  }

  async release() {
    if (this.lock) {
      try {
        await this.lock.release();
      } catch {
        /* already released */
      }
      this.lock = null;
    }
    this._hideHint();
  }

  attach() {
    this.hintEl.addEventListener("click", () => this.acquire());
    this.hintEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") this.acquire();
    });

    const tryReacquire = () => {
      if (this.requested && this.isStreaming() && !this.lock) this.acquire();
    };
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") tryReacquire();
    });
    window.addEventListener("focus", tryReacquire);
    for (const evt of ["touchstart", "click"]) {
      document.addEventListener(evt, tryReacquire, { passive: true });
    }
  }

  _showHint() {
    if (this.requested && this.isStreaming()) this.hintEl.classList.remove("hidden");
  }

  _hideHint() {
    this.hintEl.classList.add("hidden");
  }
}
