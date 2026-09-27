// Phone-side WebRTC session: sends H.264 video (and Opus mic audio) to the PC.

export class PhoneCamSession {
  /**
   * @param {import('./signaling.js').SignalChannel} signal
   * @param {{width:number,height:number,fps:number,bitrate_kbps:number,audio:boolean}} cfg
   */
  constructor(signal, cfg) {
    this.signal = signal;
    this.cfg = cfg;
    this.pc = new RTCPeerConnection({ iceServers: [] }); // LAN: host candidates only
    this.videoSender = null;
    this.audioSender = null;
    this.cameraTrack = null;
    this.micStream = null;
    this._canvasPipeline = null;
    this._closed = false;

    this.pc.onicecandidate = (e) => {
      if (e.candidate) {
        this.signal.send({ type: "candidate", candidate: e.candidate.toJSON() });
      }
    };
  }

  async start(cameraTrack) {
    this.cameraTrack = cameraTrack;
    cameraTrack.contentHint = "motion";
    this.videoSender = this.pc.addTrack(cameraTrack, new MediaStream([cameraTrack]));

    try {
      const params = this.videoSender.getParameters();
      if (!params.encodings || !params.encodings.length) params.encodings = [{}];
      params.encodings[0].maxBitrate = this.cfg.bitrate_kbps * 1000;
      params.degradationPreference = "maintain-framerate";
      await this.videoSender.setParameters(params);
    } catch {
      /* older browsers without encoder tuning */
    }

    await this._negotiate();
  }

  async _negotiate() {
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    await this._waitForIceGathering(800);
    this.signal.send({ type: "offer", sdp: this.pc.localDescription.sdp });
  }

  async handleAnswer(sdp) {
    if (this.pc.signalingState === "have-local-offer") {
      await this.pc.setRemoteDescription({ type: "answer", sdp });
    }
  }

  async addCandidate(candidate) {
    try {
      await this.pc.addIceCandidate(candidate);
    } catch {
      /* stale candidate */
    }
  }

  _waitForIceGathering(timeoutMs) {
    if (this.pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => {
        clearTimeout(timer);
        this.pc.removeEventListener("icegatheringstatechange", onChange);
        resolve();
      };
      const timer = setTimeout(done, timeoutMs);
      const onChange = () => {
        if (this.pc.iceGatheringState === "complete") done();
      };
      this.pc.addEventListener("icegatheringstatechange", onChange);
    });
  }

  // ---------- microphone (renegotiated on demand) ----------

  async enableMic() {
    if (this.audioSender) return;
    this.micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    const track = this.micStream.getAudioTracks()[0];
    this.audioSender = this.pc.addTrack(track, this.micStream);
    await this._negotiate();
  }

  async disableMic() {
    if (!this.audioSender) return;
    this.pc.removeTrack(this.audioSender);
    this.audioSender = null;
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => t.stop());
      this.micStream = null;
    }
    await this._negotiate();
  }

  // ---------- camera swap / digital zoom ----------

  async replaceCamera(track) {
    this.cameraTrack = track;
    track.contentHint = "motion";
    if (this._canvasPipeline) {
      const pipeline = this._canvasPipeline;
      this._canvasPipeline = null;
      // stop() swaps the sender back to this.cameraTrack (already the new one)
      await pipeline.stop();
    }
    if (this.videoSender) await this.videoSender.replaceTrack(track);
  }

  /**
   * Digital zoom fallback: draw a crop of <video> onto a canvas and send
   * canvas.captureStream() instead of the raw camera track.
   * @param {(ctx: CanvasRenderingContext2D) => void} cropFn
   */
  startDigitalZoom(cropFn) {
    if (this._canvasPipeline) {
      this._canvasPipeline.update(cropFn);
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = this.cfg.width;
    canvas.height = this.cfg.height;
    const ctx = canvas.getContext("2d", { alpha: false });
    const cstream = canvas.captureStream(this.cfg.fps);
    const ctrack = cstream.getVideoTracks()[0];
    let rafId = 0;
    const draw = () => {
      cropFn(ctx);
      rafId = requestAnimationFrame(draw);
    };
    rafId = requestAnimationFrame(draw);

    this._canvasPipeline = {
      update: (fn) => {
        cropFn = fn;
      },
      stop: async () => {
        cancelAnimationFrame(rafId);
        ctrack.stop();
        this._canvasPipeline = null;
        if (this.videoSender && this.cameraTrack) {
          try {
            await this.videoSender.replaceTrack(this.cameraTrack);
          } catch {
            /* sender gone */
          }
        }
      },
    };
    if (this.videoSender) {
      this.videoSender.replaceTrack(ctrack).catch(() => {});
    }
  }

  async stopDigitalZoom() {
    if (this._canvasPipeline) await this._canvasPipeline.stop();
  }

  // ---------- pause ----------

  setVideoEnabled(on) {
    if (this.videoSender && this.videoSender.track) this.videoSender.track.enabled = on;
    if (this.micStream) {
      this.micStream.getAudioTracks().forEach((t) => (t.enabled = on));
    }
  }

  // ---------- stats ----------

  async collectStats() {
    try {
      const stats = await this.pc.getStats();
      const out = {};
      stats.forEach((s) => {
        if (s.type === "outbound-rtp" && s.kind === "video") {
          out.bytes = s.bytesSent ?? 0;
          out.framesEncoded = s.framesEncoded ?? null;
          out.fps = s.framesPerSecond ?? null;
          if (s.frameWidth) out.res = `${s.frameWidth}x${s.frameHeight}`;
        } else if (s.type === "candidate-pair" && s.state === "succeeded" && s.currentRoundTripTime != null) {
          out.rttMs = Math.round(s.currentRoundTripTime * 1000);
        } else if (s.type === "remote-inbound-rtp" && s.kind === "video") {
          out.packetsLost = s.packetsLost ?? null;
          out.jitterMs = s.jitter != null ? Math.round(s.jitter * 1000) : null;
        }
      });
      return Object.keys(out).length ? out : null;
    } catch {
      return null;
    }
  }

  async close() {
    if (this._closed) return;
    this._closed = true;
    if (this._canvasPipeline) await this._canvasPipeline.stop();
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => t.stop());
      this.micStream = null;
    }
    try {
      this.pc.close();
    } catch {
      /* already closed */
    }
  }
}
