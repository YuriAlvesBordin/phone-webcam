//! PhoneCam: o celular como webcam e microfone do PC, por WebRTC na rede local.
//!
//! O celular só precisa de um navegador: manda H.264 + Opus por WebRTC; o PC
//! decodifica com GStreamer e expõe uma webcam (v4l2loopback) e um microfone
//! (PipeWire/PulseAudio) comuns.

pub mod audio;
pub mod config;
pub mod controller;
pub mod error;
pub mod hub;
pub mod media;
pub mod net;
pub mod protocol;
pub mod qr;
pub mod security;
pub mod server;
pub mod session;
pub mod status;
pub mod tls;
pub mod vcam;

pub use config::Config;
pub use controller::Controller;
pub use error::Error;
pub use status::{Phase, Status};
