// Camera capture: getUserMedia + native zoom/torch capability handling.

export class CameraController {
  constructor() {
    this.stream = null;
    this.track = null;
    this.facing = "environment";
    /** { zoom: {min,max,step} | null, torch: boolean } */
    this.capabilities = { zoom: null, torch: false };
  }

  get videoTrack() {
    return this.track;
  }

  get facingUser() {
    return this.facing === "user";
  }

  async start({ width, height, fps, facing = this.facing, videoEl = null }) {
    this.stop();

    const constraints = {
      video: {
        facingMode: { ideal: facing },
        width: { ideal: width },
        height: { ideal: height },
        frameRate: { ideal: fps },
      },
      audio: false,
    };

    this.stream = await navigator.mediaDevices.getUserMedia(constraints);
    this.track = this.stream.getVideoTracks()[0];
    this.facing = facing;
    this._detectCapabilities();

    if (videoEl) {
      videoEl.srcObject = this.stream;
      videoEl.classList.toggle("mirror", this.facingUser);
      try {
        await videoEl.play();
      } catch {
        /* autoplay may reject until a gesture; muted+playsinline usually works */
      }
    }
    return this.track;
  }

  _detectCapabilities() {
    this.capabilities = { zoom: null, torch: false };
    try {
      const caps = this.track.getCapabilities ? this.track.getCapabilities() : {};
      if (
        caps.zoom &&
        typeof caps.zoom.min === "number" &&
        typeof caps.zoom.max === "number" &&
        caps.zoom.max > caps.zoom.min
      ) {
        this.capabilities.zoom = {
          min: caps.zoom.min,
          max: caps.zoom.max,
          step: caps.zoom.step || 0.1,
        };
      }
      if (caps.torch) this.capabilities.torch = true;
    } catch {
      /* capabilities not supported */
    }
  }

  /** Native (optical/sensor) zoom via MediaTrack constraints. */
  async setNativeZoom(userZoom) {
    const z = this.capabilities.zoom;
    if (!z || !this.track) return false;
    const target = Math.min(userZoom * z.min, z.max);
    try {
      await this.track.applyConstraints({ advanced: [{ zoom: target }] });
      return true;
    } catch {
      return false;
    }
  }

  async setTorch(on) {
    if (!this.track || !this.capabilities.torch) return false;
    try {
      await this.track.applyConstraints({ advanced: [{ torch: on }] });
      return true;
    } catch {
      return false;
    }
  }

  stop() {
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
      this.track = null;
    }
  }
}
