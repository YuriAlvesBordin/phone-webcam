//! Saídas persistentes: webcam virtual (v4l2) e microfone virtual (PulseAudio).
//!
//! Ficam de pé enquanto o serviço está ligado, mesmo sem celular: a sessão
//! WebRTC só empurra mídia pelos canais `inter*` e, quando o celular sai, a
//! webcam continua existindo (imagem preta) em vez de sumir do Discord/OBS.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use gstreamer as gst;
use gstreamer::prelude::*;

use crate::audio::SINK_NAME;
use crate::config::{Config, AUDIO_SAMPLE_RATE};
use crate::error::Error;

pub const VIDEO_CHANNEL: &str = "phonecam-video";
pub const AUDIO_CHANNEL: &str = "phonecam-audio";

/// Descrição do pipeline de vídeo de saída (separada para ser testável).
pub fn video_out_description(cfg: &Config, device: &str) -> String {
    format!(
        "intervideosrc channel={VIDEO_CHANNEL} ! videoconvert ! videoscale add-borders=true ! \
         videorate ! video/x-raw,format=YUY2,width={w},height={h},framerate={fps}/1 ! \
         v4l2sink device={device} sync=false",
        w = cfg.width,
        h = cfg.height,
        fps = cfg.fps,
    )
}

pub fn audio_out_description() -> String {
    format!(
        "interaudiosrc channel={AUDIO_CHANNEL} ! \
         audio/x-raw,format=S16LE,rate={AUDIO_SAMPLE_RATE},channels=1 ! audioconvert ! \
         audioresample ! pulsesink device={SINK_NAME} buffer-time=100000 latency-time=20000"
    )
}

/// Parte final da cadeia de vídeo de uma sessão (depois do decodificador).
pub const VIDEO_SESSION_TAIL: &str = "intervideosink channel=phonecam-video sync=false";

pub const AUDIO_SESSION_TAIL: &str = "audioconvert ! audioresample ! \
     audio/x-raw,format=S16LE,rate=48000,channels=1 ! \
     interaudiosink channel=phonecam-audio sync=false";

struct Output {
    pipeline: gst::Pipeline,
    stop: Arc<AtomicBool>,
    watcher: Option<std::thread::JoinHandle<()>>,
}

impl Output {
    fn start(name: &'static str, description: &str) -> Result<Self, Error> {
        let pipeline = gst::parse::launch(description)?
            .downcast::<gst::Pipeline>()
            .map_err(|_| Error::Gst(format!("{name}: não é um pipeline")))?;
        let bus = pipeline.bus().ok_or_else(|| Error::Gst("sem bus".into()))?;
        pipeline.set_state(gst::State::Playing).map_err(|e| {
            let detail = drain_errors(&bus);
            let _ = pipeline.set_state(gst::State::Null);
            Error::Gst(format!("{name}: {e}{detail}"))
        })?;

        let stop = Arc::new(AtomicBool::new(false));
        let watcher = {
            let stop = stop.clone();
            std::thread::Builder::new()
                .name(format!("phonecam-{name}-bus"))
                .spawn(move || {
                    while !stop.load(Ordering::Relaxed) {
                        let Some(msg) = bus.timed_pop(gst::ClockTime::from_mseconds(400)) else {
                            continue;
                        };
                        if let gst::MessageView::Error(e) = msg.view() {
                            tracing::error!("{name}: {} ({:?})", e.error(), e.debug());
                        }
                    }
                })
                .ok()
        };
        Ok(Self {
            pipeline,
            stop,
            watcher,
        })
    }
}

fn drain_errors(bus: &gst::Bus) -> String {
    let mut out = String::new();
    while let Some(msg) = bus.pop_filtered(&[gst::MessageType::Error]) {
        if let gst::MessageView::Error(e) = msg.view() {
            out.push_str(&format!(": {}", e.error()));
        }
    }
    out
}

impl Drop for Output {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        let _ = self.pipeline.set_state(gst::State::Null);
        if let Some(w) = self.watcher.take() {
            let _ = w.join();
        }
    }
}

/// Webcam e microfone virtuais ativos.
pub struct Outputs {
    video: Option<Output>,
    audio: Option<Output>,
}

impl Outputs {
    /// Cada saída é opcional: se uma falha, a outra continua e o erro volta em `Err` na lista.
    pub fn start(cfg: &Config, device: Option<&str>, mic: bool) -> (Self, Vec<String>) {
        let mut problems = Vec::new();
        let video = device.and_then(|dev| {
            Output::start("video", &video_out_description(cfg, dev))
                .map_err(|e| problems.push(format!("webcam virtual: {e}")))
                .ok()
        });
        let audio = mic
            .then(|| {
                Output::start("audio", &audio_out_description())
                    .map_err(|e| problems.push(format!("microfone virtual: {e}")))
                    .ok()
            })
            .flatten();
        (Self { video, audio }, problems)
    }

    pub fn has_video(&self) -> bool {
        self.video.is_some()
    }

    pub fn has_audio(&self) -> bool {
        self.audio.is_some()
    }

    /// Dá tempo do pipeline assentar antes de ligar o servidor (evita corrida nos testes).
    pub fn settle(&self) {
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn descricoes_usam_a_resolucao_e_o_dispositivo() {
        let cfg = Config {
            width: 1920,
            height: 1080,
            fps: 60,
            ..Config::default()
        };
        let d = video_out_description(&cfg, "/dev/video10");
        assert!(d.contains("width=1920,height=1080,framerate=60/1"));
        assert!(d.contains("device=/dev/video10"));
        assert!(audio_out_description().contains("device=phonecam_mic_sink"));
    }

    #[test]
    fn descricoes_sao_pipelines_validos() {
        gst::init().unwrap();
        // fakesink no lugar do v4l2sink: valida a sintaxe e as caps sem precisar do módulo.
        let d = video_out_description(&Config::default(), "/dev/video10")
            .replace("v4l2sink device=/dev/video10 sync=false", "fakesink");
        assert!(gst::parse::launch(&d).is_ok());
        let a = audio_out_description().replace(
            "pulsesink device=phonecam_mic_sink buffer-time=100000 latency-time=20000",
            "fakesink",
        );
        assert!(gst::parse::launch(&a).is_ok());
    }
}
