//! `phonecam doctor`: o que falta no sistema, com a correção ao lado.

use phonecam_core::{audio, net, vcam};

fn line(ok: bool, what: &str, hint: &str) -> bool {
    if ok {
        println!("  ok  {what}");
    } else {
        println!("  ERRO {what}\n       -> {hint}");
    }
    ok
}

fn warn(ok: bool, what: &str, hint: &str) {
    if ok {
        println!("  ok  {what}");
    } else {
        println!("  aviso {what}\n       -> {hint}");
    }
}

pub fn run() -> Result<(), String> {
    gstreamer::init().map_err(|e| e.to_string())?;
    println!("PhoneCam - diagnóstico\n");

    let mut ok = true;
    println!("GStreamer:");
    for (el, pkg) in [
        ("webrtcbin", "gst-plugins-bad"),
        ("nicesrc", "libnice (gst-plugins-bad)"),
        ("rtph264depay", "gst-plugins-good"),
        ("avdec_h264", "gst-libav"),
        ("h264parse", "gst-plugins-bad"),
        ("rtpopusdepay", "gst-plugins-good"),
        ("opusdec", "gst-plugins-base"),
        ("intervideosrc", "gst-plugins-bad"),
        ("interaudiosrc", "gst-plugins-bad"),
        ("v4l2sink", "gst-plugins-good"),
        ("pulsesink", "gst-plugins-good"),
    ] {
        ok &= line(
            gstreamer::ElementFactory::find(el).is_some(),
            el,
            &format!("instale {pkg}"),
        );
    }
    warn(
        gstreamer::ElementFactory::find("vp8dec").is_some(),
        "vp8dec (alternativa se o navegador não oferecer H.264)",
        "gst-plugins-good",
    );

    println!("\nWebcam virtual:");
    let loaded = vcam::module_loaded();
    warn(
        loaded,
        "módulo v4l2loopback carregado",
        "sudo pacman -S v4l2loopback-dkms && veja packaging/README.md",
    );
    let dev = vcam::find(None);
    warn(
        dev.is_some(),
        &match &dev {
            Some(d) => format!("dispositivo {} ({})", d.path, d.name),
            None => "dispositivo v4l2loopback".into(),
        },
        "carregue o módulo com exclusive_caps=1 video_nr=10 card_label=PhoneCam",
    );
    if loaded {
        warn(
            vcam::exclusive_caps_ok() == Some(true),
            "exclusive_caps=1 (Discord/Chrome só listam a câmera assim)",
            "recarregue o módulo com exclusive_caps=1",
        );
    }

    println!("\nMicrofone virtual:");
    warn(
        audio::server_available(),
        "servidor PipeWire/PulseAudio (pactl)",
        "instale pipewire-pulse",
    );

    println!("\nRede:");
    let ips = net::local_ips();
    ok &= line(
        !ips.is_empty(),
        &format!(
            "IPs da LAN: {}",
            ips.iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        "conecte o PC a uma rede",
    );
    println!(
        "  info portas: TCP {} (página/sinalização), UDP {}-{} (mídia ICE)",
        phonecam_core::config::DEFAULT_PORT,
        phonecam_core::config::ICE_PORT_MIN,
        phonecam_core::config::ICE_PORT_MAX
    );

    println!();
    if ok {
        println!("Tudo que é obrigatório está presente.");
        Ok(())
    } else {
        Err("faltam dependências obrigatórias".into())
    }
}
