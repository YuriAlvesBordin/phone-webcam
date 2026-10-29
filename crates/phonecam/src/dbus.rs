//! Serviço D-Bus `dev.aqua.PhoneCam` (barra do aqua) e cliente da CLI.
//!
//! O estado viaja como JSON (`phonecam_core::Status`): um método (`GetStatus`) e um
//! sinal (`Changed`) bastam, e campos novos não quebram quem observa.

use std::sync::Arc;
use std::time::Duration;

use phonecam_core::{Config, Controller, Phase};
use zbus::object_server::SignalEmitter;

pub const BUS_NAME: &str = "dev.aqua.PhoneCam";
pub const OBJECT_PATH: &str = "/dev/aqua/PhoneCam";
pub const INTERFACE: &str = "dev.aqua.PhoneCam1";

/// O daemon encerra sozinho depois deste tempo desligado (o D-Bus o reativa sob demanda).
const IDLE_EXIT: Duration = Duration::from_secs(90);

struct Service {
    ctl: Arc<Controller>,
}

fn json(ctl: &Controller) -> String {
    serde_json::to_string(&ctl.status()).unwrap_or_else(|_| "{}".into())
}

fn fail(e: impl std::fmt::Display) -> zbus::fdo::Error {
    zbus::fdo::Error::Failed(e.to_string())
}

#[zbus::interface(name = "dev.aqua.PhoneCam1")]
impl Service {
    async fn start(&self) -> zbus::fdo::Result<String> {
        self.ctl.start().await.map_err(fail)?;
        Ok(json(&self.ctl))
    }

    async fn stop(&self) -> String {
        self.ctl.stop().await;
        json(&self.ctl)
    }

    async fn toggle(&self) -> zbus::fdo::Result<String> {
        if self.ctl.is_running().await {
            self.ctl.stop().await;
        } else {
            self.ctl.start().await.map_err(fail)?;
        }
        Ok(json(&self.ctl))
    }

    async fn get_status(&self) -> String {
        json(&self.ctl)
    }

    /// Devolve o novo PIN ("" se o serviço está desligado).
    async fn regenerate_pin(&self) -> String {
        self.ctl.regenerate_pin().await.unwrap_or_default()
    }

    async fn disconnect(&self) {
        self.ctl.disconnect().await;
    }

    #[zbus(signal)]
    async fn changed(emitter: &SignalEmitter<'_>, status: &str) -> zbus::Result<()>;
}

pub async fn run_daemon(cfg: Config) -> Result<(), String> {
    let ctl = Arc::new(Controller::new(cfg).map_err(|e| e.to_string())?);
    let conn = zbus::connection::Builder::session()
        .and_then(|b| b.name(BUS_NAME))
        .and_then(|b| b.serve_at(OBJECT_PATH, Service { ctl: ctl.clone() }))
        .map_err(|e| e.to_string())?
        .build()
        .await
        .map_err(|e| format!("não consegui registrar {BUS_NAME} no D-Bus: {e}"))?;
    tracing::info!("serviço {BUS_NAME} no ar (desligado até receber Start)");

    let iface = conn
        .object_server()
        .interface::<_, Service>(OBJECT_PATH)
        .await
        .map_err(|e| e.to_string())?;

    let mut rx = ctl.hub().subscribe();
    let mut off_since = Some(tokio::time::Instant::now());
    let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .map_err(|e| e.to_string())?;
    loop {
        let idle = async {
            match off_since {
                Some(t) => tokio::time::sleep_until(t + IDLE_EXIT).await,
                None => std::future::pending().await,
            }
        };
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            _ = sigterm.recv() => break,
            () = idle => {
                tracing::info!("desligado há {}s: encerrando o daemon", IDLE_EXIT.as_secs());
                break;
            }
            r = rx.changed() => {
                if r.is_err() { break; }
                let st = rx.borrow_and_update().clone();
                off_since = (st.phase == Phase::Off).then(tokio::time::Instant::now);
                let text = serde_json::to_string(&st).unwrap_or_default();
                if let Err(e) = Service::changed(iface.signal_emitter(), &text).await {
                    tracing::debug!("sinal Changed: {e}");
                }
            }
        }
    }
    ctl.stop().await;
    Ok(())
}

/// Chama um método do serviço e imprime o resultado.
pub async fn client(method: &str) -> Result<(), String> {
    let conn = zbus::Connection::session()
        .await
        .map_err(|e| e.to_string())?;
    let reply = conn
        .call_method(Some(BUS_NAME), OBJECT_PATH, Some(INTERFACE), method, &())
        .await
        .map_err(|e| {
            format!("{method}: {e} (o serviço está instalado? veja packaging/README.md)")
        })?;
    if let Ok(text) = reply.body().deserialize::<String>() {
        if !text.is_empty() {
            println!("{text}");
        }
    }
    Ok(())
}
