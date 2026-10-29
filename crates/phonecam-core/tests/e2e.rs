//! Ponta a ponta: um "celular" falso (webrtcbin + openh264enc + opusenc) conecta
//! pelo WebSocket real, e o teste espera o vídeo chegar ao serviço (`Phase::Live`).

use std::net::TcpListener;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use gstreamer as gst;
use gstreamer::prelude::*;
use gstreamer_sdp as gst_sdp;
use gstreamer_webrtc as gst_webrtc;
use phonecam_core::{Config, Controller, Phase};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

enum Phone {
    Offer(String),
    Candidate(u32, String),
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn text(m: &str) -> Message {
    Message::Text(m.to_string().into())
}

/// Celular falso: manda vídeo H.264 e áudio Opus de teste.
fn fake_phone(tx: mpsc::UnboundedSender<Phone>) -> (gst::Pipeline, gst::Element) {
    let pipeline = gst::parse::launch(
        "webrtcbin name=w bundle-policy=max-bundle \
         videotestsrc is-live=true pattern=smpte ! video/x-raw,width=640,height=480,framerate=30/1 ! \
           videoconvert ! openh264enc bitrate=1000000 ! h264parse ! \
           rtph264pay config-interval=-1 pt=96 ! \
           application/x-rtp,media=video,encoding-name=H264,payload=96 ! w. \
         audiotestsrc is-live=true ! audioconvert ! audioresample ! opusenc ! rtpopuspay pt=97 ! \
           application/x-rtp,media=audio,encoding-name=OPUS,payload=97 ! w.",
    )
    .unwrap()
    .downcast::<gst::Pipeline>()
    .unwrap();
    let w = pipeline.by_name("w").unwrap();

    let txc = tx.clone();
    w.connect("on-ice-candidate", false, move |v| {
        let m = v[1].get::<u32>().unwrap();
        let c = v[2].get::<String>().unwrap();
        let _ = txc.send(Phone::Candidate(m, c));
        None
    });
    let wc = w.clone();
    w.connect("on-negotiation-needed", false, move |_| {
        let w2 = wc.clone();
        let tx = tx.clone();
        let p = gst::Promise::with_change_func(move |reply| {
            let offer = reply
                .unwrap()
                .unwrap()
                .get::<gst_webrtc::WebRTCSessionDescription>("offer")
                .unwrap();
            w2.emit_by_name::<()>("set-local-description", &[&offer, &None::<gst::Promise>]);
            let _ = tx.send(Phone::Offer(offer.sdp().as_text().unwrap()));
        });
        wc.emit_by_name::<()>("create-offer", &[&None::<gst::Structure>, &p]);
        None
    });
    (pipeline, w)
}

/// Conecta o celular falso ao serviço e mantém a sinalização rodando em segundo plano.
async fn connect_phone(port: u16, pin: &str) -> (gst::Pipeline, tokio::task::JoinHandle<()>) {
    let (ws, _) = tokio_tungstenite::connect_async(format!("ws://127.0.0.1:{port}/signal"))
        .await
        .unwrap();
    let (mut sink, mut stream) = ws.split();
    sink.send(text(&format!(r#"{{"type":"auth","pin":"{pin}"}}"#)))
        .await
        .unwrap();
    let Some(Ok(Message::Text(t))) = stream.next().await else {
        panic!("sem resposta de auth");
    };
    assert!(t.contains("auth_ok"), "{t}");

    let (tx, mut rx) = mpsc::unbounded_channel();
    let (pipeline, w) = fake_phone(tx);
    pipeline.set_state(gst::State::Playing).unwrap();
    let task = tokio::spawn(async move {
        loop {
            tokio::select! {
                Some(ev) = rx.recv() => {
                    let m = match ev {
                        Phone::Offer(sdp) => serde_json::json!({"type":"offer","sdp":sdp}),
                        Phone::Candidate(mline, c) => serde_json::json!({"type":"candidate",
                            "candidate":{"candidate":c,"sdpMLineIndex":mline}}),
                    };
                    if sink.send(text(&m.to_string())).await.is_err() { return; }
                }
                msg = stream.next() => {
                    let Some(Ok(Message::Text(t))) = msg else { return };
                    let v: serde_json::Value = serde_json::from_str(&t).unwrap();
                    match v["type"].as_str() {
                        Some("answer") => {
                            let m = gst_sdp::SDPMessage::parse_buffer(v["sdp"].as_str().unwrap().as_bytes()).unwrap();
                            let ans = gst_webrtc::WebRTCSessionDescription::new(gst_webrtc::WebRTCSDPType::Answer, m);
                            w.emit_by_name::<()>("set-remote-description", &[&ans, &None::<gst::Promise>]);
                        }
                        Some("candidate") => {
                            let c = &v["candidate"];
                            w.emit_by_name::<()>("add-ice-candidate",
                                &[&(c["sdpMLineIndex"].as_u64().unwrap_or(0) as u32), &c["candidate"].as_str().unwrap()]);
                        }
                        _ => {}
                    }
                }
            }
        }
    });
    (pipeline, task)
}

#[tokio::test(flavor = "multi_thread")]
async fn video_do_celular_chega_ao_servico() {
    let port = free_port();
    let cfg = Config {
        host: "127.0.0.1".into(),
        port,
        https: false,
        audio: false,
        video: false,
        pin: Some("424242".into()),
        ..Config::default()
    };
    let ctl = Controller::new(cfg).unwrap();
    let st = ctl.start().await.unwrap();
    assert_eq!(st.phase, Phase::Waiting);
    assert_eq!(st.pin, "424242");

    let (pipeline, task) = connect_phone(port, "424242").await;
    let mut live = false;
    let mut audio_seen = false;
    let mut status = ctl.hub().subscribe();
    let deadline = tokio::time::sleep(Duration::from_secs(25));
    tokio::pin!(deadline);
    loop {
        tokio::select! {
            () = &mut deadline => break,
            Ok(()) = status.changed() => {
                let s = status.borrow().clone();
                audio_seen |= s.audio_live;
                if s.phase == Phase::Live && s.fps > 5.0 && audio_seen {
                    live = true;
                    break;
                }
            }
        }
    }

    let final_status = ctl.status();
    pipeline.set_state(gst::State::Null).unwrap();
    task.abort();
    ctl.stop().await;
    assert!(live, "o vídeo não chegou: {final_status:?}");
    assert_eq!(ctl.status().phase, Phase::Off);
}

/// Ponta a ponta até o /dev/video10: lê a webcam virtual como o Discord faria e confere que a
/// imagem do celular (barras coloridas) chega. Pulado onde o v4l2loopback não existe.
#[tokio::test(flavor = "multi_thread")]
async fn webcam_virtual_recebe_a_imagem_do_celular() {
    let _ = tracing_subscriber::fmt()
        .with_env_filter("phonecam_core=debug")
        .try_init();
    let Some(dev) = phonecam_core::vcam::find(None) else {
        eprintln!("pulado: sem v4l2loopback");
        return;
    };
    let port = free_port();
    let cfg = Config {
        host: "127.0.0.1".into(),
        port,
        https: false,
        audio: false,
        pin: Some("424242".into()),
        video_device: Some(dev.path.clone()),
        ..Config::default()
    };
    let ctl = Controller::new(cfg).unwrap();
    let st = ctl.start().await.unwrap();
    if st.camera.is_none() {
        ctl.stop().await;
        eprintln!("pulado: sem acesso a {}: {:?}", dev.path, st.warnings);
        return;
    }
    let (pipeline, task) = connect_phone(port, "424242").await;

    let mut status = ctl.hub().subscribe();
    let live = tokio::time::timeout(Duration::from_secs(25), async {
        while status.borrow().phase != Phase::Live {
            status.changed().await.unwrap();
        }
    })
    .await
    .is_ok();
    assert!(live, "o vídeo não chegou ao serviço");

    // Lê um quadro da webcam virtual e mede o contraste da luminância (Y de cada YUY2).
    let reader = gst::parse::launch(&format!(
        "v4l2src device={} ! video/x-raw,format=YUY2 ! appsink name=out sync=false",
        dev.path
    ))
    .unwrap()
    .downcast::<gst::Pipeline>()
    .unwrap();
    let appsink = reader
        .by_name("out")
        .unwrap()
        .downcast::<gstreamer_app::AppSink>()
        .unwrap();
    reader.set_state(gst::State::Playing).unwrap();
    let mut best = 0u32;
    let mut got = 0;
    let until = std::time::Instant::now() + Duration::from_secs(8);
    while std::time::Instant::now() < until && best <= 100 {
        let Some(sample) = appsink.try_pull_sample(gst::ClockTime::from_mseconds(500)) else {
            continue;
        };
        got += 1;
        let buf = sample.buffer().unwrap().map_readable().unwrap();
        let ys: Vec<u8> = buf.as_slice().iter().step_by(2).copied().collect();
        let (lo, hi) = (
            ys.iter().min().copied().unwrap_or(0),
            ys.iter().max().copied().unwrap_or(0),
        );
        best = best.max(u32::from(hi - lo));
    }
    reader.set_state(gst::State::Null).unwrap();
    pipeline.set_state(gst::State::Null).unwrap();
    task.abort();
    ctl.stop().await;
    eprintln!("quadros lidos: {got}");
    assert!(
        best > 100,
        "a webcam virtual só mostrou imagem sem contraste ({best})"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn pin_errado_e_recusado_e_bloqueia_depois_de_cinco() {
    let port = free_port();
    let cfg = Config {
        host: "127.0.0.1".into(),
        port,
        https: false,
        audio: false,
        video: false,
        pin: Some("424242".into()),
        ..Config::default()
    };
    let ctl = Controller::new(cfg).unwrap();
    ctl.start().await.unwrap();
    let url = format!("ws://127.0.0.1:{port}/signal");

    for _ in 0..5 {
        let (mut ws, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
        ws.send(text(r#"{"type":"auth","pin":"000000"}"#))
            .await
            .unwrap();
        match ws.next().await {
            Some(Ok(Message::Close(Some(f)))) => assert_eq!(u16::from(f.code), 4003),
            other => panic!("esperava close 4003, veio {other:?}"),
        }
    }
    // Sexta tentativa, mesmo com o PIN certo: bloqueada.
    let (mut ws, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    let Some(Ok(Message::Text(t))) = ws.next().await else {
        panic!("esperava aviso de bloqueio");
    };
    assert!(t.contains("locked"), "{t}");
    ctl.stop().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn paginas_e_cabecalhos_de_seguranca() {
    let port = free_port();
    let cfg = Config {
        host: "127.0.0.1".into(),
        port,
        https: false,
        audio: false,
        video: false,
        ..Config::default()
    };
    let ctl = Controller::new(cfg).unwrap();
    ctl.start().await.unwrap();

    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let get = |path: &'static str| async move {
        let mut s = tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .unwrap();
        s.write_all(
            format!("GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").as_bytes(),
        )
        .await
        .unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).await.unwrap();
        out.to_lowercase()
    };
    let index = get("/").await;
    assert!(index.starts_with("http/1.1 200"), "{index}");
    assert!(index.contains("content-security-policy"));
    assert!(index.contains("x-frame-options: deny"));
    assert!(index.contains("cache-control: no-store"));
    assert!(get("/js/app.js").await.contains("text/javascript"));
    assert!(get("/qrcode.svg").await.contains("<svg"));
    assert!(get("/nada").await.starts_with("http/1.1 404"));
    ctl.stop().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn qr_conecta_sem_pin_e_o_token_nao_vaza() {
    let port = free_port();
    let cfg = Config {
        host: "127.0.0.1".into(),
        port,
        https: false,
        audio: false,
        video: false,
        pin: Some("424242".into()),
        ..Config::default()
    };
    let ctl = Controller::new(cfg).unwrap();
    let st = ctl.start().await.unwrap();
    // O QR leva o token no fragmento; a URL exibida e os endpoints HTTP nunca o mostram.
    let token = st
        .qr_url
        .split("#k=")
        .nth(1)
        .expect("qr_url sem token")
        .to_string();
    assert_eq!(token.len(), 32);
    assert!(!st.url.contains(&token) && !st.url.contains('#'));
    let url = format!("ws://127.0.0.1:{port}/signal");

    // Token certo: entra sem PIN.
    let (mut ws, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    ws.send(text(&format!(r#"{{"type":"auth","token":"{token}"}}"#)))
        .await
        .unwrap();
    let Some(Ok(Message::Text(t))) = ws.next().await else {
        panic!("sem resposta");
    };
    assert!(t.contains("auth_ok"), "{t}");
    drop(ws);

    // Token errado e credenciais vazias: recusados.
    for bad in [
        r#"{"type":"auth","token":"00000000000000000000000000000000"}"#,
        r#"{"type":"auth"}"#,
        r#"{"type":"auth","pin":"","token":""}"#,
    ] {
        let (mut ws, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
        ws.send(text(bad)).await.unwrap();
        match ws.next().await {
            Some(Ok(Message::Close(Some(f)))) => assert_eq!(u16::from(f.code), 4003, "{bad}"),
            other => panic!("{bad}: {other:?}"),
        }
    }

    // Nenhum endpoint HTTP pode conter o token.
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    for path in ["/qrcode", "/qrcode.svg", "/info"] {
        let mut s = tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .unwrap();
        s.write_all(
            format!("GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").as_bytes(),
        )
        .await
        .unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).await.unwrap();
        assert!(!out.contains(&token), "{path} vazou o token");
    }

    // Novo PIN invalida o QR antigo.
    ctl.regenerate_pin().await.unwrap();
    let (mut ws, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
    ws.send(text(&format!(r#"{{"type":"auth","token":"{token}"}}"#)))
        .await
        .unwrap();
    assert!(
        matches!(ws.next().await, Some(Ok(Message::Close(Some(f)))) if u16::from(f.code) == 4003)
    );
    assert_ne!(ctl.status().qr_url, st.qr_url);
    ctl.stop().await;
}
