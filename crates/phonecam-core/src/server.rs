//! Servidor HTTPS: interface do celular, sinalização WebSocket e diagnóstico.

use std::net::SocketAddr;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::extract::{ConnectInfo, Path, State};
use axum::http::{header, HeaderMap, HeaderName, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use include_dir::{include_dir, Dir};
use tokio::sync::{mpsc, Notify};
use tower_http::set_header::SetResponseHeaderLayer;

use crate::config::{
    Config, AUDIO_SAMPLE_RATE, AUTH_LOCKOUT_S, AUTH_MAX_FAILURES, AUTH_TIMEOUT_S,
    MAX_SIGNAL_MSG_BYTES,
};
use crate::hub::Hub;
use crate::protocol::{self, close, ClientMsg, IceCandidate, MediaCfg, ServerMsg, StatsMsg};
use crate::security::{origin_allowed, pin_matches, RateLimiter};

struct Creds {
    pin: String,
    token: String,
}
use crate::session::{Session, SessionEvent};
use crate::status::Phase;

static STATIC_DIR: Dir = include_dir!("$CARGO_MANIFEST_DIR/../../static");

const CSP: &str = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; \
                   script-src 'self'; connect-src 'self' ws: wss:; media-src 'self' blob:; \
                   frame-ancestors 'none'; base-uri 'none'";

pub struct App {
    pub cfg: Config,
    pub hub: Hub,
    creds: RwLock<Creds>,
    url: String,
    audio: bool,
    limiter: Mutex<RateLimiter>,
    /// Sinal para derrubar a sessão atual (outro celular entrou, ou pedido do usuário).
    current: Mutex<Option<(u64, Arc<Notify>)>>,
    next_id: Mutex<u64>,
    kick_reason: Mutex<&'static str>,
}

impl App {
    pub fn new(
        cfg: Config,
        hub: Hub,
        pin: String,
        token: String,
        url: String,
        audio: bool,
    ) -> Arc<Self> {
        Arc::new(Self {
            limiter: Mutex::new(RateLimiter::new(
                AUTH_MAX_FAILURES,
                Duration::from_secs(AUTH_LOCKOUT_S),
            )),
            cfg,
            hub,
            creds: RwLock::new(Creds { pin, token }),
            url,
            audio,
            current: Mutex::new(None),
            next_id: Mutex::new(0),
            kick_reason: Mutex::new("another client connected"),
        })
    }

    /// PIN e token válidos agora.
    pub fn creds_match(&self, pin: &str, token: &str) -> bool {
        let Ok(c) = self.creds.read() else {
            return false;
        };
        // Sem curto-circuito entre os dois: o tempo não revela qual deles errou.
        let by_pin = !pin.is_empty() && pin_matches(pin, &c.pin);
        let by_token = !token.is_empty() && pin_matches(token, &c.token);
        by_pin | by_token
    }

    /// Troca as credenciais (novo PIN invalida o QR antigo).
    pub fn set_creds(&self, pin: String, token: String) {
        if let Ok(mut c) = self.creds.write() {
            *c = Creds { pin, token };
        }
    }

    /// Derruba o celular conectado (se houver).
    pub fn kick_current(&self, reason: &'static str) {
        if let Some((_, n)) = self.current.lock().ok().and_then(|c| c.clone()) {
            if let Ok(mut r) = self.kick_reason.lock() {
                *r = reason;
            }
            n.notify_one();
        }
    }

    /// Registra a sessão nova e derruba a anterior. Devolve (id, sinal de kick).
    fn replace_current(&self) -> (u64, Arc<Notify>) {
        let id = {
            let mut n = self.next_id.lock().expect("next_id");
            *n += 1;
            *n
        };
        let notify = Arc::new(Notify::new());
        let old = self
            .current
            .lock()
            .expect("current")
            .replace((id, notify.clone()));
        if let Some((_, old)) = old {
            if let Ok(mut r) = self.kick_reason.lock() {
                *r = "another client connected";
            }
            old.notify_one();
        }
        (id, notify)
    }

    /// Libera o posto se ainda for desta sessão. `true` quando era a atual.
    fn clear_current(&self, id: u64) -> bool {
        let mut cur = self.current.lock().expect("current");
        if cur.as_ref().is_some_and(|(cid, _)| *cid == id) {
            *cur = None;
            true
        } else {
            false
        }
    }
}

pub fn router(app: Arc<App>) -> Router {
    let hdr = |name: &'static str, value: &'static str| {
        SetResponseHeaderLayer::if_not_present(
            HeaderName::from_static(name),
            HeaderValue::from_static(value),
        )
    };
    Router::new()
        .route("/", get(index))
        .route("/info", get(info))
        .route("/qrcode.svg", get(qr_svg))
        .route("/qrcode", get(qr_text))
        .route("/signal", get(signal))
        .route("/manifest.webmanifest", get(asset_manifest))
        .route("/icon.svg", get(asset_icon))
        .route("/js/{*path}", get(asset_js))
        .route("/css/{*path}", get(asset_css))
        .layer(hdr("content-security-policy", CSP))
        .layer(hdr("x-content-type-options", "nosniff"))
        .layer(hdr("referrer-policy", "no-referrer"))
        .layer(hdr("x-frame-options", "DENY"))
        .with_state(app)
}

fn content_type(path: &str) -> &'static str {
    match path.rsplit('.').next().unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "json" => "application/json",
        "webmanifest" => "application/manifest+json",
        _ => "application/octet-stream",
    }
}

/// Os assets sempre revalidam: celulares (Samsung Internet) guardam módulos ES agressivamente.
fn static_file(path: &str) -> Response {
    match STATIC_DIR.get_file(path) {
        Some(f) => (
            [
                (header::CONTENT_TYPE, content_type(path)),
                (header::CACHE_CONTROL, "no-store"),
            ],
            f.contents(),
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

async fn index() -> Response {
    static_file("index.html")
}

async fn asset_manifest() -> Response {
    static_file("manifest.webmanifest")
}

async fn asset_icon() -> Response {
    static_file("icon.svg")
}

async fn asset_js(Path(p): Path<String>) -> Response {
    static_file(&format!("js/{p}"))
}

async fn asset_css(Path(p): Path<String>) -> Response {
    static_file(&format!("css/{p}"))
}

async fn info(State(app): State<Arc<App>>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "transport": "webrtc",
        "width": app.cfg.width,
        "height": app.cfg.height,
        "fps": app.cfg.fps,
        "bitrate_kbps": app.cfg.bitrate_kbps,
        "pin_required": true,
        "audio_enabled": app.audio,
        "audio_sample_rate": AUDIO_SAMPLE_RATE,
    }))
}

async fn qr_svg(State(app): State<Arc<App>>) -> Response {
    match crate::qr::svg(&app.url) {
        Some(svg) => ([(header::CONTENT_TYPE, "image/svg+xml")], svg).into_response(),
        None => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

async fn qr_text(State(app): State<Arc<App>>) -> Response {
    let qr = crate::qr::terminal(&app.url).unwrap_or_default();
    (
        [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
        format!("PhoneCam\nURL: {}\n\n{qr}\n", app.url),
    )
        .into_response()
}

async fn signal(
    State(app): State<Arc<App>>,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> Response {
    let get = |name| headers.get(name).and_then(|v| v.to_str().ok());
    let allowed = origin_allowed(get(header::ORIGIN), get(header::HOST));
    ws.max_message_size(MAX_SIGNAL_MSG_BYTES)
        .on_upgrade(move |socket| async move {
            if !allowed {
                tracing::warn!("sinalização recusada: Origin inválido de {}", addr.ip());
                close_with(socket, close::BAD_ORIGIN, "Bad Origin").await;
                return;
            }
            run_client(app, socket, addr.ip().to_string()).await;
        })
}

async fn close_with(mut ws: WebSocket, code: u16, reason: &'static str) {
    let _ = ws
        .send(Message::Close(Some(CloseFrame {
            code,
            reason: reason.into(),
        })))
        .await;
}

async fn send(ws: &mut WebSocket, msg: &ServerMsg<'_>) -> bool {
    ws.send(Message::Text(msg.to_json().into())).await.is_ok()
}

/// Lê a próxima mensagem de texto; `None` se o celular saiu.
async fn recv_text(ws: &mut WebSocket) -> Option<String> {
    loop {
        match ws.recv().await? {
            Ok(Message::Text(t)) => return Some(t.to_string()),
            Ok(Message::Close(_)) | Err(_) => return None,
            Ok(_) => {}
        }
    }
}

enum Auth {
    Ok,
    BadPin,
    Locked,
}

async fn authenticate(app: &App, ws: &mut WebSocket, ip: &str) -> Auth {
    let locked = app
        .limiter
        .lock()
        .expect("limiter")
        .lockout_remaining(ip, Instant::now());
    if let Some(left) = locked {
        let _ = send(
            ws,
            &ServerMsg::Error {
                code: "locked",
                retry_after_s: left.as_secs_f32(),
            },
        )
        .await;
        tracing::warn!(
            "cliente bloqueado recusado: {ip} ({:.0}s)",
            left.as_secs_f32()
        );
        return Auth::Locked;
    }

    let first = tokio::time::timeout(Duration::from_secs(AUTH_TIMEOUT_S), recv_text(ws)).await;
    let Ok(Some(raw)) = first else {
        return Auth::BadPin;
    };
    let ok = matches!(
        protocol::parse(&raw),
        Some(ClientMsg::Auth { pin, token }) if app.creds_match(&pin, &token)
    );
    let mut limiter = app.limiter.lock().expect("limiter");
    if ok {
        limiter.reset(ip);
    } else {
        limiter.record_failure(ip, Instant::now());
        tracing::warn!("PIN errado de {ip}");
    }
    if ok {
        Auth::Ok
    } else {
        Auth::BadPin
    }
}

async fn run_client(app: Arc<App>, mut ws: WebSocket, ip: String) {
    match authenticate(&app, &mut ws, &ip).await {
        Auth::Ok => {}
        Auth::BadPin => return close_with(ws, close::BAD_PIN, "Invalid PIN").await,
        Auth::Locked => return close_with(ws, close::LOCKED, "Too many failed attempts").await,
    }

    let auth_ok = ServerMsg::AuthOk {
        cfg: MediaCfg {
            width: app.cfg.width,
            height: app.cfg.height,
            fps: app.cfg.fps,
            bitrate_kbps: app.cfg.bitrate_kbps,
            audio: app.audio,
        },
    };
    if !send(&mut ws, &auth_ok).await {
        return;
    }
    tracing::info!("celular autenticado: {ip}");

    let (events_tx, events_rx) = mpsc::unbounded_channel();
    let session = match Session::new(events_tx) {
        Ok(s) => s,
        Err(e) => {
            tracing::error!("não consegui criar a sessão WebRTC: {e}");
            close_with(ws, 1011, "internal error").await;
            return;
        }
    };

    let (id, kick) = app.replace_current();
    app.hub.update(|s| {
        s.phase = Phase::Connecting;
        s.client = ip.clone();
        s.fps = 0.0;
        s.bitrate_kbps = 0.0;
        s.audio_live = false;
    });

    drive(&app, &mut ws, &session, events_rx, &kick).await;

    session.close().await;
    if app.clear_current(id) {
        app.hub.update(|s| {
            s.phase = Phase::Waiting;
            s.client.clear();
            s.fps = 0.0;
            s.bitrate_kbps = 0.0;
            s.audio_live = false;
        });
    }
    tracing::info!("celular desconectado: {ip}");
}

/// Laço principal da sessão: mensagens do celular, eventos do pipeline e kick.
async fn drive(
    app: &Arc<App>,
    ws: &mut WebSocket,
    session: &Session,
    mut events: mpsc::UnboundedReceiver<SessionEvent>,
    kick: &Notify,
) {
    use gstreamer_webrtc::WebRTCPeerConnectionState as Peer;
    loop {
        tokio::select! {
            () = kick.notified() => {
                let reason = app.kick_reason.lock().map(|r| *r).unwrap_or("kicked");
                let _ = send(ws, &ServerMsg::Bye { reason }).await;
                close_with_ref(ws, close::KICKED).await;
                return;
            }
            msg = ws.recv() => {
                let Some(Ok(msg)) = msg else { return };
                let Message::Text(text) = msg else {
                    if matches!(msg, Message::Close(_)) { return; }
                    continue;
                };
                match protocol::parse(&text) {
                    Some(ClientMsg::Offer { sdp }) => match session.handle_offer(&sdp).await {
                        Ok(answer) => {
                            if !send(ws, &ServerMsg::Answer { sdp: &answer }).await { return; }
                        }
                        Err(e) => {
                            tracing::warn!("oferta recusada: {e}");
                            return;
                        }
                    },
                    Some(ClientMsg::Candidate { candidate: Some(c) }) => {
                        session.add_candidate(c.sdp_m_line_index.unwrap_or(0), &c.candidate);
                    }
                    Some(ClientMsg::Bye) => return,
                    _ => {}
                }
            }
            ev = events.recv() => {
                let Some(ev) = ev else { return };
                match ev {
                    SessionEvent::Candidate { mline, candidate } => {
                        let c = IceCandidate {
                            candidate,
                            sdp_mid: None,
                            sdp_m_line_index: Some(mline),
                        };
                        if !send(ws, &ServerMsg::Candidate { candidate: c }).await { return; }
                    }
                    SessionEvent::PeerState(Peer::Failed | Peer::Closed) => return,
                    SessionEvent::PeerState(_) => {}
                    SessionEvent::VideoStarted => app.hub.update(|s| s.phase = Phase::Live),
                    SessionEvent::Stats(st) => {
                        app.hub.update(|s| {
                            s.fps = round1(st.fps);
                            s.bitrate_kbps = round1(st.bitrate_kbps);
                            s.packets_lost = st.packets_lost;
                            s.jitter_ms = round1(st.jitter_ms);
                            s.audio_live = st.audio_live;
                        });
                        let msg = ServerMsg::Stats(StatsMsg {
                            fps: round1(st.fps),
                            frames: st.frames,
                            bitrate_kbps: round1(st.bitrate_kbps),
                            packets_lost: st.packets_lost,
                            jitter_ms: round1(st.jitter_ms),
                        });
                        if !send(ws, &msg).await { return; }
                    }
                    SessionEvent::Dead(why) => {
                        tracing::warn!("sessão encerrada: {why}");
                        return;
                    }
                }
            }
        }
    }
}

async fn close_with_ref(ws: &mut WebSocket, code: u16) {
    let _ = ws
        .send(Message::Close(Some(CloseFrame {
            code,
            reason: "kicked".into(),
        })))
        .await;
}

fn round1(v: f32) -> f32 {
    (v * 10.0).round() / 10.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tipos_de_conteudo() {
        assert!(content_type("js/app.js").starts_with("text/javascript"));
        assert!(content_type("css/styles.css").starts_with("text/css"));
        assert!(content_type("index.html").starts_with("text/html"));
        assert_eq!(content_type("x.bin"), "application/octet-stream");
    }

    #[test]
    fn interface_do_celular_esta_embutida() {
        for f in [
            "index.html",
            "js/app.js",
            "js/signaling.js",
            "css/styles.css",
            "icon.svg",
            "manifest.webmanifest",
        ] {
            assert!(STATIC_DIR.get_file(f).is_some(), "falta {f}");
        }
        assert_eq!(
            static_file("js/nao-existe.js").status(),
            StatusCode::NOT_FOUND
        );
    }

    #[test]
    fn arredonda_uma_casa() {
        assert_eq!(round1(29.96), 30.0);
        assert_eq!(round1(1.24), 1.2);
    }
}
