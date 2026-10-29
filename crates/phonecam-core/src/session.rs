//! Uma sessão WebRTC (um celular) em cima do `webrtcbin` do GStreamer.
//!
//! O `webrtcbin` cuida de ICE/DTLS/SRTP/jitter. Cada trilha recebida ganha uma
//! cadeia depay -> decodificador -> canal `inter*`, que alimenta as saídas
//! persistentes de `media`.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use gstreamer as gst;
use gstreamer::prelude::*;
use gstreamer_sdp as gst_sdp;
use gstreamer_webrtc as gst_webrtc;
use gstreamer_webrtc::{WebRTCICEGatheringState, WebRTCPeerConnectionState};
use tokio::sync::{mpsc, oneshot};

use crate::config::{ICE_PORT_MAX, ICE_PORT_MIN, INACTIVITY_TIMEOUT_S, STATS_INTERVAL_S};
use crate::error::Error;
use crate::media::{AUDIO_SESSION_TAIL, VIDEO_SESSION_TAIL};

/// Buffer do jitterbuffer do webrtcbin (ms): LAN Wi-Fi tolera pouco e mantém a latência baixa.
const JITTER_LATENCY_MS: u32 = 80;
/// Espera máxima pelos candidatos locais antes de mandar a resposta.
const GATHER_TIMEOUT: Duration = Duration::from_millis(1000);
/// Prazo para o primeiro quadro depois da resposta.
const FIRST_FRAME_TIMEOUT_S: u64 = 20;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct StatsSample {
    pub fps: f32,
    pub frames: u64,
    pub bitrate_kbps: f32,
    pub packets_lost: i64,
    pub jitter_ms: f32,
    pub audio_live: bool,
}

#[derive(Debug)]
pub enum SessionEvent {
    /// Candidato ICE local (para o celular).
    Candidate {
        mline: u32,
        candidate: String,
    },
    PeerState(WebRTCPeerConnectionState),
    /// Primeiro quadro decodificado.
    VideoStarted,
    Stats(StatsSample),
    /// Sem quadros pelo prazo, ou erro fatal do pipeline.
    Dead(String),
}

#[derive(Default)]
struct Counters {
    frames: AtomicU64,
    bytes: AtomicU64,
    audio_buffers: AtomicU64,
}

pub struct Session {
    pipeline: gst::Pipeline,
    webrtc: gst::Element,
    closed: Arc<AtomicBool>,
    answered: Arc<AtomicBool>,
    tasks: std::sync::Mutex<Vec<tokio::task::JoinHandle<()>>>,
}

type PromiseResult = Result<Option<gst::Structure>, gst::PromiseError>;

fn promise() -> (gst::Promise, oneshot::Receiver<PromiseResult>) {
    let (tx, rx) = oneshot::channel();
    let p = gst::Promise::with_change_func(move |res| {
        let _ = tx.send(res.map(|o| o.map(ToOwned::to_owned)));
    });
    (p, rx)
}

async fn wait(rx: oneshot::Receiver<PromiseResult>) -> Result<Option<gst::Structure>, Error> {
    rx.await
        .map_err(|_| Error::Gst("promessa descartada".into()))?
        .map_err(|e| Error::Gst(format!("promessa: {e:?}")))
}

/// Número de qualquer tipo inteiro/real num campo de estatística.
fn num(s: &gst::StructureRef, key: &str) -> Option<f64> {
    s.get::<f64>(key)
        .ok()
        .or_else(|| s.get::<i64>(key).ok().map(|v| v as f64))
        .or_else(|| s.get::<u64>(key).ok().map(|v| v as f64))
        .or_else(|| s.get::<u32>(key).ok().map(f64::from))
        .or_else(|| s.get::<i32>(key).ok().map(f64::from))
}

/// Receita da cadeia de decodificação para um RTP de entrada, ou `None` se o codec não é suportado.
pub fn chain_description(media: &str, encoding: &str) -> Option<String> {
    let queue = "queue leaky=downstream max-size-buffers=8 max-size-bytes=0 max-size-time=0";
    match (media, encoding.to_ascii_uppercase().as_str()) {
        ("video", "H264") => Some(format!(
            "{queue} ! rtph264depay name=depay request-keyframe=true wait-for-keyframe=true ! \
             h264parse ! avdec_h264 max-threads=4 ! {VIDEO_SESSION_TAIL}"
        )),
        ("video", "VP8") => Some(format!(
            "{queue} ! rtpvp8depay name=depay request-keyframe=true wait-for-keyframe=true ! \
             vp8dec ! {VIDEO_SESSION_TAIL}"
        )),
        ("audio", "OPUS") => Some(format!(
            "{queue} ! rtpopusdepay name=depay ! opusdec ! {AUDIO_SESSION_TAIL}"
        )),
        _ => None,
    }
}

fn add_probe_counter(
    bin: &gst::Element,
    elem: &str,
    pad: &str,
    f: impl Fn(&gst::Buffer) + Send + Sync + 'static,
) {
    let Some(pad) = bin
        .downcast_ref::<gst::Bin>()
        .and_then(|b| b.by_name(elem))
        .and_then(|e| e.static_pad(pad))
    else {
        return;
    };
    pad.add_probe(gst::PadProbeType::BUFFER, move |_, info| {
        if let Some(gst::PadProbeData::Buffer(b)) = &info.data {
            f(b);
        }
        gst::PadProbeReturn::Ok
    });
}

impl Session {
    pub fn new(events: mpsc::UnboundedSender<SessionEvent>) -> Result<Self, Error> {
        let pipeline = gst::Pipeline::new();
        let webrtc = gst::ElementFactory::make("webrtcbin")
            .name("webrtc")
            .property_from_str("bundle-policy", "max-bundle")
            .property("latency", JITTER_LATENCY_MS)
            .build()
            .map_err(|_| Error::Gst("elemento webrtcbin ausente (gst-plugins-bad)".into()))?;
        pipeline.add(&webrtc)?;

        restrict_ice(&webrtc);

        let counters = Arc::new(Counters::default());
        let closed = Arc::new(AtomicBool::new(false));
        let answered = Arc::new(AtomicBool::new(false));

        {
            let events = events.clone();
            webrtc.connect("on-ice-candidate", false, move |values| {
                let mline = values.get(1)?.get::<u32>().ok()?;
                let candidate = values.get(2)?.get::<String>().ok()?;
                let _ = events.send(SessionEvent::Candidate { mline, candidate });
                None
            });
        }
        {
            let events = events.clone();
            webrtc.connect_notify(Some("connection-state"), move |el, _| {
                let st = el.property::<WebRTCPeerConnectionState>("connection-state");
                let _ = events.send(SessionEvent::PeerState(st));
            });
        }
        {
            let (pl, counters, events) = (pipeline.downgrade(), counters.clone(), events.clone());
            webrtc.connect_pad_added(move |_, pad| {
                if let Some(pl) = pl.upgrade() {
                    if let Err(e) = attach_stream(&pl, pad, &counters, &events) {
                        tracing::warn!("trilha ignorada: {e}");
                    }
                }
            });
        }

        // Erros do pipeline (decodificador quebrado, etc.) viram `Dead`.
        if let Some(bus) = pipeline.bus() {
            let (events, closed) = (events.clone(), closed.clone());
            std::thread::Builder::new()
                .name("phonecam-session-bus".into())
                .spawn(move || {
                    while !closed.load(Ordering::Relaxed) {
                        if let Some(msg) = bus.timed_pop(gst::ClockTime::from_mseconds(400)) {
                            if let gst::MessageView::Error(e) = msg.view() {
                                let _ = events.send(SessionEvent::Dead(e.error().to_string()));
                            }
                        }
                    }
                })
                .map_err(|e| Error::Gst(e.to_string()))?;
        }

        pipeline.set_state(gst::State::Playing)?;

        let stats = tokio::spawn(stats_loop(
            webrtc.clone(),
            counters,
            closed.clone(),
            answered.clone(),
            events,
        ));
        Ok(Self {
            pipeline,
            webrtc,
            closed,
            answered,
            tasks: std::sync::Mutex::new(vec![stats]),
        })
    }

    /// Aplica uma oferta (inicial ou renegociação) e devolve o SDP da resposta,
    /// já com os candidatos locais (o celular conecta sem esperar o trickle).
    pub async fn handle_offer(&self, sdp: &str) -> Result<String, Error> {
        let msg = gst_sdp::SDPMessage::parse_buffer(sdp.as_bytes())
            .map_err(|_| Error::Gst("SDP da oferta inválido".into()))?;
        let offer =
            gst_webrtc::WebRTCSessionDescription::new(gst_webrtc::WebRTCSDPType::Offer, msg);

        let (p, rx) = promise();
        self.webrtc
            .emit_by_name::<()>("set-remote-description", &[&offer, &p]);
        wait(rx).await?;

        let (p, rx) = promise();
        self.webrtc
            .emit_by_name::<()>("create-answer", &[&None::<gst::Structure>, &p]);
        let reply = wait(rx)
            .await?
            .ok_or_else(|| Error::Gst("create-answer sem resposta".into()))?;
        let answer = reply
            .get::<gst_webrtc::WebRTCSessionDescription>("answer")
            .map_err(|e| Error::Gst(format!("resposta sem 'answer': {e}")))?;

        let (p, rx) = promise();
        self.webrtc
            .emit_by_name::<()>("set-local-description", &[&answer, &p]);
        wait(rx).await?;

        // Coleta de candidatos host: leva poucos ms na LAN.
        let deadline = Instant::now() + GATHER_TIMEOUT;
        while Instant::now() < deadline
            && self
                .webrtc
                .property::<WebRTCICEGatheringState>("ice-gathering-state")
                != WebRTCICEGatheringState::Complete
        {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }

        let local = self
            .webrtc
            .property::<Option<gst_webrtc::WebRTCSessionDescription>>("local-description")
            .unwrap_or(answer);
        self.answered.store(true, Ordering::Relaxed);
        local
            .sdp()
            .as_text()
            .map_err(|e| Error::Gst(format!("SDP local: {e}")))
    }

    pub fn add_candidate(&self, mline: u32, candidate: &str) {
        if candidate.is_empty() {
            return; // marcador de fim de candidatos
        }
        self.webrtc
            .emit_by_name::<()>("add-ice-candidate", &[&mline, &candidate]);
    }

    pub async fn close(&self) {
        if self.closed.swap(true, Ordering::SeqCst) {
            return;
        }
        for t in self
            .tasks
            .lock()
            .map(|mut t| std::mem::take(&mut *t))
            .unwrap_or_default()
        {
            t.abort();
        }
        let pipeline = self.pipeline.clone();
        let _ = tokio::task::spawn_blocking(move || pipeline.set_state(gst::State::Null)).await;
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.closed.store(true, Ordering::SeqCst);
        let _ = self.pipeline.set_state(gst::State::Null);
    }
}

/// Só rede local: sem STUN/TURN, sem UPnP (não pede ao roteador para abrir portas) e com
/// faixa de portas UDP fixa (firewall).
fn restrict_ice(webrtc: &gst::Element) {
    let Some(ice) = webrtc.property::<Option<gst::glib::Object>>("ice-agent") else {
        return;
    };
    for (name, port) in [
        ("min-rtp-port", ICE_PORT_MIN),
        ("max-rtp-port", ICE_PORT_MAX),
    ] {
        if ice.find_property(name).is_some() {
            ice.set_property(name, port);
        }
    }
    if ice.find_property("agent").is_some() {
        if let Some(agent) = ice.property::<Option<gst::glib::Object>>("agent") {
            if agent.find_property("upnp").is_some() {
                agent.set_property("upnp", false);
            }
        }
    }
}

/// UPnP do agente ICE (só para teste).
#[cfg(test)]
fn upnp_enabled(webrtc: &gst::Element) -> Option<bool> {
    let ice = webrtc.property::<Option<gst::glib::Object>>("ice-agent")?;
    let agent = ice.property::<Option<gst::glib::Object>>("agent")?;
    agent.find_property("upnp")?;
    Some(agent.property::<bool>("upnp"))
}

/// Liga uma trilha recebida do `webrtcbin` à cadeia de decodificação.
fn attach_stream(
    pipeline: &gst::Pipeline,
    pad: &gst::Pad,
    counters: &Arc<Counters>,
    events: &mpsc::UnboundedSender<SessionEvent>,
) -> Result<(), Error> {
    if pad.direction() != gst::PadDirection::Src {
        return Ok(());
    }
    let caps = pad.current_caps().unwrap_or_else(|| pad.query_caps(None));
    let s = caps
        .structure(0)
        .ok_or_else(|| Error::Gst("trilha sem caps".into()))?;
    let media = s.get::<&str>("media").unwrap_or("");
    let encoding = s.get::<&str>("encoding-name").unwrap_or("");
    let desc = chain_description(media, encoding)
        .ok_or_else(|| Error::Gst(format!("codec não suportado: {media}/{encoding}")))?;
    tracing::info!("trilha recebida: {media}/{encoding}");

    let bin = gst::parse::bin_from_description(&desc, true)?;
    let bin_el = bin.upcast_ref::<gst::Element>().clone();
    pipeline.add(&bin)?;

    let is_video = media == "video";
    if is_video {
        let c = counters.clone();
        add_probe_counter(&bin_el, "depay", "sink", move |b| {
            c.bytes.fetch_add(b.size() as u64, Ordering::Relaxed);
        });
        let (c, events) = (counters.clone(), events.clone());
        add_last_sink_probe(&bin, move |_| {
            if c.frames.fetch_add(1, Ordering::Relaxed) == 0 {
                let _ = events.send(SessionEvent::VideoStarted);
            }
        });
    } else {
        let c = counters.clone();
        add_last_sink_probe(&bin, move |_| {
            c.audio_buffers.fetch_add(1, Ordering::Relaxed);
        });
    }

    bin.sync_state_with_parent()?;
    let sink = bin
        .static_pad("sink")
        .ok_or_else(|| Error::Gst("cadeia sem pad de entrada".into()))?;
    pad.link(&sink)
        .map_err(|e| Error::Gst(format!("link da trilha: {e:?}")))?;
    Ok(())
}

/// Contagem no pad de entrada do último elemento da cadeia (o `inter*sink`).
fn add_last_sink_probe(bin: &gst::Bin, f: impl Fn(&gst::Buffer) + Send + Sync + 'static) {
    let last = bin.iterate_sorted().into_iter().flatten().last();
    if let Some(pad) = last.and_then(|e| e.static_pad("sink")) {
        pad.add_probe(gst::PadProbeType::BUFFER, move |_, info| {
            if let Some(gst::PadProbeData::Buffer(b)) = &info.data {
                f(b);
            }
            gst::PadProbeReturn::Ok
        });
    }
}

async fn stats_loop(
    webrtc: gst::Element,
    counters: Arc<Counters>,
    closed: Arc<AtomicBool>,
    answered: Arc<AtomicBool>,
    events: mpsc::UnboundedSender<SessionEvent>,
) {
    let period = Duration::from_secs(STATS_INTERVAL_S);
    let mut tick = tokio::time::interval(period);
    tick.tick().await;
    let (mut last_frames, mut last_bytes, mut last_audio) = (0u64, 0u64, 0u64);
    let mut last_progress = Instant::now();
    let mut answered_at: Option<Instant> = None;
    let mut seen_video = false;

    while !closed.load(Ordering::Relaxed) {
        tick.tick().await;
        let frames = counters.frames.load(Ordering::Relaxed);
        let bytes = counters.bytes.load(Ordering::Relaxed);
        let audio = counters.audio_buffers.load(Ordering::Relaxed);
        let dt = period.as_secs_f32();

        if frames > last_frames {
            last_progress = Instant::now();
            seen_video = true;
        }
        if answered.load(Ordering::Relaxed) && answered_at.is_none() {
            answered_at = Some(Instant::now());
        }

        // Vigia: nenhum quadro no prazo (depois do primeiro, ou da resposta) encerra a sessão.
        let stalled = if seen_video {
            last_progress.elapsed() > Duration::from_secs(INACTIVITY_TIMEOUT_S)
        } else {
            answered_at.is_some_and(|t| t.elapsed() > Duration::from_secs(FIRST_FRAME_TIMEOUT_S))
        };
        if stalled {
            let _ = events.send(SessionEvent::Dead(format!(
                "sem vídeo há mais de {INACTIVITY_TIMEOUT_S}s"
            )));
            return;
        }

        let (packets_lost, jitter_ms) = read_rtp_stats(&webrtc).await;
        let sample = StatsSample {
            fps: (frames - last_frames) as f32 / dt,
            frames,
            bitrate_kbps: (bytes - last_bytes) as f32 * 8.0 / dt / 1000.0,
            packets_lost,
            jitter_ms,
            audio_live: audio > last_audio,
        };
        (last_frames, last_bytes, last_audio) = (frames, bytes, audio);
        if events.send(SessionEvent::Stats(sample)).is_err() {
            return;
        }
    }
}

/// Perdas e jitter do `inbound-rtp` de vídeo, via `get-stats` do webrtcbin.
async fn read_rtp_stats(webrtc: &gst::Element) -> (i64, f32) {
    let (p, rx) = promise();
    webrtc.emit_by_name::<()>("get-stats", &[&None::<gst::Pad>, &p]);
    let Ok(Ok(Some(reply))) = tokio::time::timeout(Duration::from_millis(500), wait(rx)).await
    else {
        return (0, 0.0);
    };
    let (mut lost, mut jitter) = (0i64, 0f32);
    for (_, value) in reply.iter() {
        let Ok(st) = value.get::<gst::Structure>() else {
            continue;
        };
        let Ok(ty) = st.get::<gst_webrtc::WebRTCStatsType>("type") else {
            continue;
        };
        if ty == gst_webrtc::WebRTCStatsType::InboundRtp
            && st.get::<&str>("kind").map_or(true, |k| k == "video")
        {
            lost = num(&st, "packets-lost").unwrap_or(0.0) as i64;
            jitter = (num(&st, "jitter").unwrap_or(0.0) * 1000.0) as f32;
        }
    }
    (lost, jitter)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn ice_sem_upnp() {
        gst::init().unwrap();
        let (tx, _rx) = mpsc::unbounded_channel();
        let session = Session::new(tx).unwrap();
        assert_eq!(upnp_enabled(&session.webrtc), Some(false));
        session.close().await;
    }

    #[test]
    fn cadeias_por_codec() {
        assert!(chain_description("video", "H264")
            .unwrap()
            .contains("avdec_h264"));
        assert!(chain_description("video", "h264").is_some());
        assert!(chain_description("video", "VP8")
            .unwrap()
            .contains("vp8dec"));
        assert!(chain_description("audio", "opus")
            .unwrap()
            .contains("opusdec"));
        assert!(chain_description("video", "AV1").is_none());
        assert!(chain_description("audio", "PCMU").is_none());
    }

    #[test]
    fn cadeias_sao_pipelines_validos() {
        gst::init().unwrap();
        for (m, e) in [("video", "H264"), ("video", "VP8"), ("audio", "OPUS")] {
            let d = chain_description(m, e).unwrap();
            assert!(
                gst::parse::bin_from_description(&d, true).is_ok(),
                "cadeia inválida: {d}"
            );
        }
    }
}
