//! Mensagens JSON da sinalização (WebSocket `/signal`).
//!
//! ```text
//! celular -> PC : {"type":"auth","pin":"123456"}
//! PC -> celular : {"type":"auth_ok","cfg":{...}}      (ou fecha com 4003)
//! celular -> PC : {"type":"offer","sdp":"..."}        (inicial e renegociações)
//! PC -> celular : {"type":"answer","sdp":"..."}
//! nos dois      : {"type":"candidate","candidate":{...}}
//! PC -> celular : {"type":"stats",...}                (periódico)
//! PC -> celular : {"type":"bye","reason":"..."}       (substituído por outro celular)
//! ```
//! O PIN só viaja na primeira mensagem, nunca na URL.

use serde::{Deserialize, Serialize};

/// Códigos de fechamento do WebSocket.
pub mod close {
    pub const BAD_ORIGIN: u16 = 1008;
    pub const TOO_BIG: u16 = 1009;
    pub const BAD_PIN: u16 = 4003;
    pub const LOCKED: u16 = 4029;
    pub const KICKED: u16 = 4009;
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMsg {
    Auth {
        #[serde(default)]
        pin: String,
        /// Credencial do QR (alternativa ao PIN).
        #[serde(default)]
        token: String,
    },
    Offer {
        sdp: String,
    },
    Candidate {
        #[serde(default)]
        candidate: Option<IceCandidate>,
    },
    Bye,
    #[serde(other)]
    Unknown,
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct IceCandidate {
    #[serde(default)]
    pub candidate: String,
    #[serde(default)]
    pub sdp_mid: Option<String>,
    #[serde(default)]
    pub sdp_m_line_index: Option<u32>,
}

#[derive(Debug, Serialize)]
pub struct MediaCfg {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate_kbps: u32,
    pub audio: bool,
}

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMsg<'a> {
    AuthOk { cfg: MediaCfg },
    Answer { sdp: &'a str },
    Candidate { candidate: IceCandidate },
    Stats(StatsMsg),
    Bye { reason: &'a str },
    Error { code: &'a str, retry_after_s: f32 },
}

#[derive(Debug, Serialize)]
pub struct StatsMsg {
    pub fps: f32,
    pub frames: u64,
    pub bitrate_kbps: f32,
    pub packets_lost: i64,
    pub jitter_ms: f32,
}

impl ServerMsg<'_> {
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "{}".into())
    }
}

pub fn parse(text: &str) -> Option<ClientMsg> {
    serde_json::from_str(text).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auth_e_offer() {
        assert_eq!(
            parse(r#"{"type":"auth","pin":"123456"}"#),
            Some(ClientMsg::Auth {
                pin: "123456".into(),
                token: String::new()
            })
        );
        assert_eq!(
            parse(r#"{"type":"auth","token":"abc"}"#),
            Some(ClientMsg::Auth {
                pin: String::new(),
                token: "abc".into()
            })
        );
        assert_eq!(
            parse(r#"{"type":"offer","sdp":"v=0"}"#),
            Some(ClientMsg::Offer { sdp: "v=0".into() })
        );
        assert_eq!(parse(r#"{"type":"bye"}"#), Some(ClientMsg::Bye));
    }

    #[test]
    fn candidato_do_navegador() {
        let m = parse(
            r#"{"type":"candidate","candidate":{"candidate":"candidate:1 1 UDP 1 10.0.0.2 5000 typ host","sdpMid":"0","sdpMLineIndex":0,"usernameFragment":"x"}}"#,
        );
        let Some(ClientMsg::Candidate { candidate: Some(c) }) = m else {
            panic!("{m:?}");
        };
        assert!(c.candidate.starts_with("candidate:"));
        assert_eq!(c.sdp_m_line_index, Some(0));
    }

    #[test]
    fn tipo_desconhecido_e_lixo() {
        assert_eq!(parse(r#"{"type":"coisa-nova"}"#), Some(ClientMsg::Unknown));
        assert_eq!(parse("não é json"), None);
        assert_eq!(parse(r#"{"sem":"tipo"}"#), None);
    }

    #[test]
    fn mensagens_do_servidor() {
        let j = ServerMsg::Answer { sdp: "v=0" }.to_json();
        assert_eq!(j, r#"{"type":"answer","sdp":"v=0"}"#);
        let j = ServerMsg::Error {
            code: "locked",
            retry_after_s: 12.5,
        }
        .to_json();
        assert!(j.contains(r#""code":"locked""#) && j.contains("12.5"));
        let j = ServerMsg::Stats(StatsMsg {
            fps: 30.0,
            frames: 9,
            bitrate_kbps: 1.5,
            packets_lost: 0,
            jitter_ms: 0.0,
        })
        .to_json();
        assert!(j.starts_with(r#"{"type":"stats""#) && j.contains(r#""fps":30.0"#));
    }
}
