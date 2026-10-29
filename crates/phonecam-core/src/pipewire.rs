//! A webcam virtual para apps que usam o PipeWire (Firefox/Meet via portal, OBS).
//!
//! Com `exclusive_caps=1` o v4l2loopback só vira dispositivo de captura depois que alguém escreve
//! nele. O WirePlumber olha o dispositivo uma vez (no boot, sem escritor) e não cria o nó de
//! vídeo; só reiniciando o WirePlumber com a webcam já viva ele aparece.

use std::process::Command;

/// `pw-dump` mostra um `Video/Source` do PhoneCam?
pub fn has_camera_source(pw_dump_json: &str) -> bool {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(pw_dump_json) else {
        return false;
    };
    v.as_array().is_some_and(|objs| {
        objs.iter().any(|o| {
            let props = &o["info"]["props"];
            props["media.class"] == "Video/Source"
                && ["node.description", "node.name", "device.description"]
                    .iter()
                    .filter_map(|k| props[k].as_str())
                    .any(|s| s.to_ascii_lowercase().contains("phonecam"))
        })
    })
}

/// `None` se não há PipeWire para perguntar.
pub fn camera_visible() -> Option<bool> {
    let out = Command::new("pw-dump").output().ok()?;
    out.status
        .success()
        .then(|| has_camera_source(&String::from_utf8_lossy(&out.stdout)))
}

/// Reinicia o WirePlumber do usuário (cerca de 2 s sem roteamento de áudio, uma vez por boot).
pub fn restart_wireplumber() -> bool {
    Command::new("systemctl")
        .args(["--user", "restart", "wireplumber"])
        .status()
        .is_ok_and(|s| s.success())
}

#[cfg(test)]
mod tests {
    use super::*;

    const WITH: &str = r#"[
      {"id":1,"info":{"props":{"media.class":"Audio/Sink","node.description":"G935"}}},
      {"id":45,"info":{"props":{"media.class":"Video/Source","node.description":"PhoneCam (V4L2)"}}}
    ]"#;

    #[test]
    fn acha_a_camera() {
        assert!(has_camera_source(WITH));
    }

    #[test]
    fn nao_confunde_com_audio_nem_com_outra_camera() {
        let no = r#"[
          {"info":{"props":{"media.class":"Audio/Source","node.description":"PhoneCam Mic"}}},
          {"info":{"props":{"media.class":"Video/Source","node.description":"Integrated Webcam"}}}
        ]"#;
        assert!(!has_camera_source(no));
        assert!(!has_camera_source("[]"));
        assert!(!has_camera_source("lixo"));
    }
}
