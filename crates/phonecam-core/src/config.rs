//! Configuração em tempo de execução e constantes nomeadas.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

pub const DEFAULT_PORT: u16 = 8765;
pub const DEFAULT_WIDTH: u32 = 1280;
pub const DEFAULT_HEIGHT: u32 = 720;
pub const DEFAULT_FPS: u32 = 30;
pub const DEFAULT_BITRATE_KBPS: u32 = 4000;
pub const PIN_LENGTH: usize = 6;

/// Sem frames de vídeo por este tempo, a sessão é encerrada.
pub const INACTIVITY_TIMEOUT_S: u64 = 15;
/// O celular precisa autenticar nesta janela depois de conectar.
pub const AUTH_TIMEOUT_S: u64 = 5;
/// Tamanho máximo de uma mensagem de sinalização (SDP + ICE são pequenos).
pub const MAX_SIGNAL_MSG_BYTES: usize = 64 * 1024;
pub const AUTH_MAX_FAILURES: u32 = 5;
pub const AUTH_LOCKOUT_S: u64 = 60;
pub const STATS_INTERVAL_S: u64 = 2;

pub const AUDIO_SAMPLE_RATE: u32 = 48_000;

/// Faixa de portas UDP do ICE (fixa, para liberar no firewall se houver um).
pub const ICE_PORT_MIN: u32 = 40_000;
pub const ICE_PORT_MAX: u32 = 40_100;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate_kbps: u32,
    /// PIN fixo; `None` gera um novo a cada início.
    pub pin: Option<String>,
    pub https: bool,
    pub audio: bool,
    /// Liga a webcam virtual (v4l2loopback). Desligada, só o microfone funciona.
    pub video: bool,
    /// Reinicia o WirePlumber (uma vez) se o PipeWire não enxergar a webcam virtual.
    pub fix_pipewire: bool,
    /// O QR já leva a credencial: escanear conecta sem digitar o PIN.
    pub qr_login: bool,
    /// Dispositivo v4l2loopback; `None` detecta sozinho.
    pub video_device: Option<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            host: "0.0.0.0".into(),
            port: DEFAULT_PORT,
            width: DEFAULT_WIDTH,
            height: DEFAULT_HEIGHT,
            fps: DEFAULT_FPS,
            bitrate_kbps: DEFAULT_BITRATE_KBPS,
            pin: None,
            https: true,
            audio: true,
            video: true,
            fix_pipewire: true,
            qr_login: true,
            video_device: None,
        }
    }
}

impl Config {
    /// `~/.config/phonecam/config.toml`
    pub fn path() -> Option<PathBuf> {
        dirs::config_dir().map(|d| d.join("phonecam").join("config.toml"))
    }

    /// Lê o arquivo de configuração; ausente ou inválido cai no padrão (com aviso).
    pub fn load() -> Self {
        let Some(path) = Self::path() else {
            return Self::default();
        };
        match std::fs::read_to_string(&path) {
            Ok(text) => Self::from_toml(&text).unwrap_or_else(|e| {
                tracing::warn!("{}: {e}; usando o padrão", path.display());
                Self::default()
            }),
            Err(_) => Self::default(),
        }
    }

    pub fn from_toml(text: &str) -> Result<Self, toml::de::Error> {
        toml::from_str(text)
    }

    /// Diretório de dados (certificados).
    pub fn data_dir() -> PathBuf {
        dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("phonecam")
    }

    pub fn validate(&self) -> Result<(), String> {
        if !(16..=7680).contains(&self.width) || !(16..=4320).contains(&self.height) {
            return Err(format!(
                "resolução inválida: {}x{}",
                self.width, self.height
            ));
        }
        if !(1..=120).contains(&self.fps) {
            return Err(format!("fps inválido: {}", self.fps));
        }
        if let Some(pin) = &self.pin {
            if pin.len() != PIN_LENGTH || !pin.bytes().all(|b| b.is_ascii_digit()) {
                return Err(format!("o PIN deve ter {PIN_LENGTH} dígitos"));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn padrao_e_valido() {
        assert!(Config::default().validate().is_ok());
    }

    #[test]
    fn toml_parcial_completa_com_padrao() {
        let c = Config::from_toml("port = 9000\nfps = 60").unwrap();
        assert_eq!((c.port, c.fps, c.width), (9000, 60, DEFAULT_WIDTH));
    }

    #[test]
    fn rejeita_pin_e_fps_ruins() {
        let mut c = Config {
            pin: Some("12ab56".into()),
            ..Config::default()
        };
        assert!(c.validate().is_err());
        c.pin = Some("123456".into());
        assert!(c.validate().is_ok());
        c.fps = 0;
        assert!(c.validate().is_err());
    }
}
