//! `phonecam`: CLI e serviço D-Bus.
//!
//! - `phonecam serve`   roda em primeiro plano com banner e QR (uso avulso).
//! - `phonecam daemon`  serviço D-Bus `dev.aqua.PhoneCam` (barra do aqua); começa desligado.
//! - `phonecam start|stop|toggle|status|pin|disconnect` falam com o serviço.
//! - `phonecam doctor`  confere o que falta no sistema.

mod dbus;
mod doctor;

use clap::{Parser, Subcommand};
use phonecam_core::{Config, Controller};

#[derive(Parser)]
#[command(
    name = "phonecam",
    version,
    about = "Use o celular como webcam e microfone do PC (WebRTC)"
)]
struct Cli {
    #[command(subcommand)]
    cmd: Option<Cmd>,
    /// Log detalhado
    #[arg(short, long, global = true)]
    verbose: bool,
}

#[derive(clap::Args, Clone)]
struct Overrides {
    #[arg(long)]
    host: Option<String>,
    #[arg(long)]
    port: Option<u16>,
    #[arg(long)]
    width: Option<u32>,
    #[arg(long)]
    height: Option<u32>,
    #[arg(long)]
    fps: Option<u32>,
    /// Bitrate máximo de vídeo (kbps)
    #[arg(long)]
    bitrate: Option<u32>,
    /// PIN fixo (padrão: aleatório a cada início)
    #[arg(long)]
    pin: Option<String>,
    /// O QR não conecta sozinho: exige digitar o PIN no celular
    #[arg(long)]
    no_qr_login: bool,
    /// HTTP puro (só localhost; celulares exigem HTTPS)
    #[arg(long)]
    no_https: bool,
    #[arg(long)]
    no_audio: bool,
    /// Sem webcam virtual (só o microfone)
    #[arg(long)]
    no_video: bool,
    /// Dispositivo v4l2loopback (padrão: detecta)
    #[arg(long)]
    device: Option<String>,
}

impl Overrides {
    fn apply(self, mut cfg: Config) -> Config {
        macro_rules! set {
            ($($f:ident),*) => {$( if let Some(v) = self.$f { cfg.$f = v; } )*};
        }
        set!(host, port, width, height, fps);
        if let Some(v) = self.bitrate {
            cfg.bitrate_kbps = v;
        }
        if self.pin.is_some() {
            cfg.pin = self.pin;
        }
        if self.device.is_some() {
            cfg.video_device = self.device;
        }
        cfg.qr_login &= !self.no_qr_login;
        cfg.https &= !self.no_https;
        cfg.audio &= !self.no_audio;
        cfg.video &= !self.no_video;
        cfg
    }
}

#[derive(Subcommand)]
enum Cmd {
    /// Roda em primeiro plano (padrão)
    Serve(#[command(flatten)] Overrides),
    /// Serviço D-Bus para a barra do aqua
    Daemon(#[command(flatten)] Overrides),
    /// Liga o serviço (ativa o daemon se preciso)
    Start,
    /// Desliga o serviço
    Stop,
    /// Alterna ligado/desligado
    Toggle,
    /// Estado atual em JSON
    Status,
    /// Gera um novo PIN
    Pin,
    /// Desconecta o celular atual
    Disconnect,
    /// Confere dependências do sistema
    Doctor,
}

fn init_logging(verbose: bool) {
    let default = if verbose {
        "phonecam=debug,phonecam_core=debug"
    } else {
        "phonecam=info,phonecam_core=info"
    };
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(default));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .init();
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    init_logging(cli.verbose);
    let code = match run(cli).await {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("erro: {e}");
            1
        }
    };
    std::process::exit(code);
}

async fn run(cli: Cli) -> Result<(), String> {
    match cli.cmd.unwrap_or(Cmd::Serve(Overrides::empty())) {
        Cmd::Serve(o) => serve(o.apply(Config::load())).await,
        Cmd::Daemon(o) => dbus::run_daemon(o.apply(Config::load())).await,
        Cmd::Doctor => doctor::run(),
        Cmd::Start => dbus::client("Start").await,
        Cmd::Stop => dbus::client("Stop").await,
        Cmd::Toggle => dbus::client("Toggle").await,
        Cmd::Status => dbus::client("GetStatus").await,
        Cmd::Pin => dbus::client("RegeneratePin").await,
        Cmd::Disconnect => dbus::client("Disconnect").await,
    }
}

impl Overrides {
    fn empty() -> Self {
        Self {
            host: None,
            port: None,
            width: None,
            height: None,
            fps: None,
            bitrate: None,
            pin: None,
            no_qr_login: false,
            no_https: false,
            no_audio: false,
            no_video: false,
            device: None,
        }
    }
}

async fn serve(cfg: Config) -> Result<(), String> {
    let ctl = Controller::new(cfg.clone()).map_err(|e| e.to_string())?;
    let st = ctl.start().await.map_err(|e| e.to_string())?;
    print_banner(&cfg, &st);

    let mut rx = ctl.hub().subscribe();
    let mut last = st.phase;
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            r = rx.changed() => {
                if r.is_err() { break; }
                let s = rx.borrow_and_update().clone();
                if s.phase != last {
                    last = s.phase;
                    println!("  [{:?}] {}", s.phase, s.client);
                }
            }
        }
    }
    println!("\nDesligando...");
    ctl.stop().await;
    Ok(())
}

fn print_banner(cfg: &Config, st: &phonecam_core::Status) {
    println!(
        "\n{}\n  PhoneCam {} - celular como webcam (WebRTC)\n{}\n",
        "=".repeat(64),
        env!("CARGO_PKG_VERSION"),
        "=".repeat(64)
    );
    println!(
        "  Vídeo     : {}x{} @ {}fps (H.264, máx {} kbps)",
        cfg.width, cfg.height, cfg.fps, cfg.bitrate_kbps
    );
    println!(
        "  Webcam    : {}",
        st.camera.as_deref().unwrap_or("indisponível")
    );
    println!(
        "  Microfone : {}",
        if st.microphone {
            "PhoneCam Mic"
        } else {
            "desligado/indisponível"
        }
    );
    println!("  PIN       : {}\n", st.pin);
    if let Some(qr) = phonecam_core::qr::terminal(&st.qr_url) {
        for line in qr.lines() {
            println!("      {line}");
        }
    }
    println!(
        "\n  Escaneie o QR: conecta sozinho. Ou abra {} e digite o PIN.",
        st.url
    );
    if cfg.https {
        println!("\n  O navegador vai avisar sobre o certificado (é autoassinado, é normal).");
    }
    for w in &st.warnings {
        println!("  ! {w}");
    }
    println!("\n  Ctrl+C para parar.\n");
}
