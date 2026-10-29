//! Liga e desliga o serviço inteiro: microfone/webcam virtuais, certificado e servidor.

use std::net::{SocketAddr, TcpListener};
use std::sync::Arc;

use tokio::sync::Mutex;
use tokio::task::JoinHandle;

use crate::audio::{self, VirtualMic};
use crate::config::Config;
use crate::error::Error;
use crate::hub::Hub;
use crate::media::Outputs;
use crate::net::{connection_url, local_ips};
use crate::security::{generate_pin, generate_token};
use crate::server::{router, App};
use crate::status::{Phase, Status};
use crate::{tls, vcam};

struct Running {
    app: Arc<App>,
    handle: axum_server::Handle,
    task: JoinHandle<()>,
    // A ordem importa no Drop: saídas antes do microfone (o pulsesink usa o sink nulo).
    _outputs: Outputs,
    _mic: Option<VirtualMic>,
}

pub struct Controller {
    cfg: Config,
    hub: Hub,
    running: Mutex<Option<Running>>,
}

impl Controller {
    pub fn new(cfg: Config) -> Result<Self, Error> {
        cfg.validate().map_err(Error::Config)?;
        gstreamer::init()?;
        let _ = rustls::crypto::ring::default_provider().install_default();
        Ok(Self {
            cfg,
            hub: Hub::new(),
            running: Mutex::new(None),
        })
    }

    pub fn hub(&self) -> &Hub {
        &self.hub
    }

    pub fn config(&self) -> &Config {
        &self.cfg
    }

    pub fn status(&self) -> Status {
        self.hub.snapshot()
    }

    /// Liga tudo. Se já estiver ligado, devolve o estado atual.
    pub async fn start(&self) -> Result<Status, Error> {
        let mut slot = self.running.lock().await;
        if slot.is_some() {
            return Ok(self.hub.snapshot());
        }
        let cfg = self.cfg.clone();

        let mut warnings = Vec::new();

        // Microfone primeiro: a saída de áudio toca no sink que ele cria.
        let mic = if cfg.audio {
            match VirtualMic::create() {
                Ok(m) => Some(m),
                Err(e) => {
                    warnings.push(format!("microfone virtual: {e}"));
                    None
                }
            }
        } else {
            None
        };

        let device = if cfg.video {
            vcam::find(cfg.video_device.as_deref())
        } else {
            None
        };
        match &device {
            None if !vcam::module_loaded() => warnings.push(
                "webcam virtual indisponível: módulo v4l2loopback não carregado \
                 (veja packaging/README.md)"
                    .into(),
            ),
            None => warnings.push("nenhum dispositivo v4l2loopback encontrado".into()),
            Some(_) if vcam::exclusive_caps_ok() == Some(false) => warnings.push(
                "v4l2loopback sem exclusive_caps=1: Discord e Chrome não vão listar a câmera"
                    .into(),
            ),
            Some(_) => {}
        }

        let (outputs, problems) = Outputs::start(
            &cfg,
            device.as_ref().map(|d| d.path.as_str()),
            mic.is_some(),
        );
        warnings.extend(problems);
        outputs.settle();

        let ips = local_ips();
        let cert = if cfg.https {
            Some(tls::ensure_cert(&Config::data_dir().join("certs"), &ips)?)
        } else {
            None
        };

        let pin = cfg.pin.clone().unwrap_or_else(generate_pin);
        let token = generate_token();
        let host = ips.first().copied();
        let url = connection_url(cfg.https, host, cfg.port, None);
        let qr_url = if cfg.qr_login {
            connection_url(cfg.https, host, cfg.port, Some(&token))
        } else {
            url.clone()
        };

        let addr: SocketAddr = format!("{}:{}", cfg.host, cfg.port)
            .parse()
            .map_err(|e| Error::Config(format!("endereço inválido: {e}")))?;
        let listener = TcpListener::bind(addr)
            .map_err(|e| Error::Other(format!("não consegui abrir {addr}: {e}")))?;
        listener.set_nonblocking(true)?;

        let app = App::new(
            cfg.clone(),
            self.hub.clone(),
            pin.clone(),
            token,
            url.clone(),
            mic.is_some(),
        );
        let handle = axum_server::Handle::new();
        let service = router(app.clone()).into_make_service_with_connect_info::<SocketAddr>();
        let task = {
            let handle = handle.clone();
            match cert {
                Some(c) => {
                    let tls = axum_server::tls_rustls::RustlsConfig::from_pem_file(&c.cert, &c.key)
                        .await
                        .map_err(|e| Error::Tls(e.to_string()))?;
                    tokio::spawn(async move {
                        let srv = axum_server::from_tcp_rustls(listener, tls).handle(handle);
                        if let Err(e) = srv.serve(service).await {
                            tracing::error!("servidor HTTPS: {e}");
                        }
                    })
                }
                None => tokio::spawn(async move {
                    let srv = axum_server::from_tcp(listener).handle(handle);
                    if let Err(e) = srv.serve(service).await {
                        tracing::error!("servidor HTTP: {e}");
                    }
                }),
            }
        };

        self.hub.update(|s| {
            *s = Status {
                phase: Phase::Waiting,
                url,
                qr_url,
                pin,
                width: cfg.width,
                height: cfg.height,
                camera: device.map(|d| d.path).filter(|_| outputs.has_video()),
                microphone: outputs.has_audio(),
                warnings,
                ..Status::default()
            };
        });
        for w in &self.hub.snapshot().warnings {
            tracing::warn!("{w}");
        }

        *slot = Some(Running {
            app,
            handle,
            task,
            _outputs: outputs,
            _mic: mic,
        });
        Ok(self.hub.snapshot())
    }

    /// Desliga e fecha a porta. Desfaz o microfone virtual.
    pub async fn stop(&self) {
        let Some(Running {
            app,
            handle,
            task,
            _outputs,
            _mic,
        }) = self.running.lock().await.take()
        else {
            return;
        };
        app.kick_current("server stopped");
        handle.graceful_shutdown(Some(std::time::Duration::from_millis(800)));
        let _ = task.await;
        drop((_outputs, _mic));
        self.hub.update(|s| *s = Status::default());
    }

    /// Novo PIN sem derrubar quem já está conectado.
    pub async fn regenerate_pin(&self) -> Option<String> {
        let slot = self.running.lock().await;
        let running = slot.as_ref()?;
        let (pin, token) = (generate_pin(), generate_token());
        running.app.set_creds(pin.clone(), token.clone());
        let host = local_ips().first().copied();
        let qr_url = if self.cfg.qr_login {
            connection_url(self.cfg.https, host, self.cfg.port, Some(&token))
        } else {
            connection_url(self.cfg.https, host, self.cfg.port, None)
        };
        self.hub.update(|s| {
            s.pin = pin.clone();
            s.qr_url = qr_url;
        });
        Some(pin)
    }

    /// Desconecta o celular atual, mantendo o serviço ligado.
    pub async fn disconnect(&self) {
        if let Some(r) = self.running.lock().await.as_ref() {
            r.app.kick_current("disconnected by the PC");
        }
    }

    pub async fn is_running(&self) -> bool {
        self.running.lock().await.is_some()
    }
}

/// Atalho para diagnósticos (`phonecam doctor`).
pub fn audio_server_available() -> bool {
    audio::server_available()
}
