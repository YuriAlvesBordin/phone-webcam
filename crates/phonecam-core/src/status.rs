//! Estado publicado pelo serviço (D-Bus, barra, CLI). É o contrato com quem
//! mostra o estado: campos novos só podem ser adicionados, com `#[serde(default)]`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Servidor desligado: nenhuma porta aberta.
    #[default]
    Off,
    /// Servidor no ar, esperando o celular.
    Waiting,
    /// Celular autenticado, negociando a conexão.
    Connecting,
    /// Vídeo chegando.
    Live,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Status {
    pub phase: Phase,
    pub url: String,
    pub pin: String,
    /// IP do celular conectado.
    pub client: String,
    pub width: u32,
    pub height: u32,
    pub fps: f32,
    pub bitrate_kbps: f32,
    pub packets_lost: i64,
    pub jitter_ms: f32,
    pub rtt_ms: f32,
    /// Dispositivo da webcam virtual (ex.: /dev/video10), se existir.
    pub camera: Option<String>,
    /// Microfone virtual criado?
    pub microphone: bool,
    /// Celular está mandando áudio?
    pub audio_live: bool,
    /// Avisos para o usuário (ex.: v4l2loopback ausente).
    pub warnings: Vec<String>,
}

impl Status {
    pub fn is_on(&self) -> bool {
        self.phase != Phase::Off
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_tolerante_a_campos_novos_e_ausentes() {
        let s: Status = serde_json::from_str(r#"{"phase":"live","fps":29.5,"futuro":1}"#).unwrap();
        assert_eq!(s.phase, Phase::Live);
        assert!(s.is_on());
        assert!(s.warnings.is_empty());
    }

    #[test]
    fn ida_e_volta() {
        let s = Status {
            phase: Phase::Waiting,
            url: "https://x/".into(),
            warnings: vec!["a".into()],
            ..Default::default()
        };
        let back: Status = serde_json::from_str(&serde_json::to_string(&s).unwrap()).unwrap();
        assert_eq!(back, s);
    }
}
