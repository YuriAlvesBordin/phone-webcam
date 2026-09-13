// Signaling channel: WebSocket to the PC + PIN handshake + JSON routing.
// The PIN travels in the first authenticated message, never in the URL.

export class SignalError extends Error {
  constructor(message, code, retryAfterS = null) {
    super(message);
    this.name = "SignalError";
    this.code = code;
    this.retryAfterS = retryAfterS;
  }
}

export class SignalChannel {
  constructor() {
    this.ws = null;
    /** @type {((msg: object) => void) | null} post-auth server messages */
    this.onmessage = null;
    /** @type {((ev: CloseEvent) => void) | null} post-auth close events */
    this.onclose = null;
  }

  get isOpen() {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      try {
        this.ws = new WebSocket(`${proto}//${location.host}/signal`);
      } catch {
        reject(new SignalError("Não foi possível criar WebSocket.", 0));
        return;
      }
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => {
        /* onclose follows */
      };
      this.ws.onclose = (ev) => {
        if (this.ws && this.ws.readyState !== WebSocket.OPEN && !this._authed) {
          reject(new SignalError(`Conexão recusada (${ev.code}).`, ev.code));
        }
      };
    });
  }

  authenticate(pin) {
    return new Promise((resolve, reject) => {
      const ws = this.ws;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new SignalError("WebSocket não está aberto.", 0));
        return;
      }

      const timeout = setTimeout(() => {
        cleanup();
        reject(new SignalError("Tempo esgotado na autenticação.", 0));
      }, 8000);

      const onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "auth_ok") {
          cleanup();
          this._authed = true;
          this._wireRouting();
          resolve(msg.cfg || {});
        } else if (msg.type === "error" && msg.code === "locked") {
          cleanup();
          const secs = Math.ceil(msg.retry_after_s || 0);
          reject(new SignalError(`Bloqueado por tentativas erradas - aguarde ${secs}s.`, 4029, msg.retry_after_s));
        } else {
          cleanup();
          reject(new SignalError("Resposta inesperada do servidor.", 0));
        }
      };

      const onclose = (ev) => {
        cleanup();
        if (ev.code === 4003) {
          reject(new SignalError("PIN inválido. Verifique no terminal do PC.", 4003));
        } else if (ev.code === 4005) {
          reject(new SignalError("Servidor sem WebRTC (aiortc). Atualize o servidor.", 4005));
        } else if (ev.code === 4029) {
          reject(new SignalError("Bloqueado por tentativas erradas - aguarde.", 4029));
        } else {
          reject(new SignalError(`Conexão encerrada durante autenticação (${ev.code}).`, ev.code));
        }
      };

      const cleanup = () => {
        clearTimeout(timeout);
        ws.onmessage = null;
        ws.onclose = null;
      };

      ws.onmessage = onmessage;
      ws.onclose = onclose;
      ws.send(JSON.stringify({ type: "auth", pin }));
    });
  }

  _wireRouting() {
    this.ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (this.onmessage) this.onmessage(msg);
    };
    this.ws.onclose = (ev) => {
      if (this.onclose) this.onclose(ev);
    };
    this.ws.onerror = () => {
      /* onclose follows */
    };
  }

  send(obj) {
    if (this.isOpen) this.ws.send(JSON.stringify(obj));
  }

  close() {
    this._authed = false;
    this.onmessage = null;
    this.onclose = null;
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* already closed */
      }
      this.ws = null;
    }
  }
}
