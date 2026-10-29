//! Microfone virtual no PipeWire/PulseAudio.
//!
//! ```text
//! sink nulo "phonecam_mic_sink" (SAÍDA)  <- o pipeline toca o áudio do celular aqui
//!          | monitor
//!          v
//! fonte virtual "PhoneCam Mic" (ENTRADA) <- Discord/OBS escolhem esta
//! ```
//! A fonte virtual existe porque o Discord não lista "Monitor of ..." como entrada.

use std::process::{Child, Command, Stdio};

pub const SINK_NAME: &str = "phonecam_mic_sink";
pub const SOURCE_NAME: &str = "phonecam_mic";
pub const SOURCE_DESC: &str = "PhoneCam Mic";

/// IDs dos módulos `kind` cujo `sink_name=`/`source_name=` bate com `name`
/// (saída de `pactl list short modules`).
pub fn module_ids(list: &str, kind: &str, key: &str, name: &str) -> Vec<String> {
    let needle = format!("{key}={name}");
    list.lines()
        .filter(|l| l.contains(kind) && l.split_whitespace().any(|w| w == needle))
        .filter_map(|l| l.split_whitespace().next().map(str::to_string))
        .collect()
}

fn pactl(args: &[&str]) -> Option<String> {
    let out = Command::new("pactl").args(args).output().ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
}

pub fn server_available() -> bool {
    pactl(&["info"]).is_some()
}

fn unload_old(list: &str) {
    let ids = module_ids(list, "module-virtual-source", "source_name", SOURCE_NAME)
        .into_iter()
        .chain(module_ids(list, "module-null-sink", "sink_name", SINK_NAME));
    for id in ids {
        let _ = pactl(&["unload-module", &id]);
    }
}

/// Dono dos módulos criados; ao sair (Drop) desfaz tudo.
pub struct VirtualMic {
    modules: Vec<String>,
    loopback: Option<Child>,
}

impl VirtualMic {
    pub fn create() -> Result<Self, String> {
        if !server_available() {
            return Err("sem servidor PipeWire/PulseAudio (pactl info falhou)".into());
        }
        if let Some(list) = pactl(&["list", "short", "modules"]) {
            unload_old(&list);
        }

        let sink = pactl(&[
            "load-module",
            "module-null-sink",
            &format!("sink_name={SINK_NAME}"),
            "sink_properties=device.description='PhoneCam Mic Sink'",
            "channels=1",
            "rate=48000",
            "channel_map=mono",
        ])
        .ok_or("não consegui criar o sink nulo")?;
        let mut mic = Self {
            modules: vec![sink],
            loopback: None,
        };

        let source = pactl(&[
            "load-module",
            "module-virtual-source",
            &format!("source_name={SOURCE_NAME}"),
            &format!(
                "source_properties=device.description='{SOURCE_DESC}',\
                 device.icon_name='audio-input-microphone',media.class='Audio/Source'"
            ),
            &format!("master={SINK_NAME}.monitor"),
            "rate=48000",
            "channels=1",
            "channel_map=mono",
            "use_system_clock_for_timing=yes",
        ]);
        match source {
            Some(id) => mic.modules.push(id),
            None => {
                mic.loopback =
                    Some(spawn_pw_loopback().ok_or(
                        "nem module-virtual-source nem pw-loopback criaram a fonte virtual",
                    )?)
            }
        }
        Ok(mic)
    }
}

fn spawn_pw_loopback() -> Option<Child> {
    let playback = format!(
        r#"{{"node.name":"{SOURCE_NAME}","node.description":"{SOURCE_DESC}","media.class":"Audio/Source","device.icon_name":"audio-input-microphone"}}"#
    );
    let capture = format!(r#"{{"node.name":"{SOURCE_NAME}_capture","media.class":"Audio/Sink"}}"#);
    let mut child = Command::new("pw-loopback")
        .args(["--capture", &format!("{SINK_NAME}.monitor")])
        .arg(format!("--playback-props={playback}"))
        .arg(format!("--capture-props={capture}"))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    std::thread::sleep(std::time::Duration::from_millis(600));
    match child.try_wait() {
        Ok(None) => Some(child),
        _ => None,
    }
}

impl Drop for VirtualMic {
    fn drop(&mut self) {
        if let Some(mut c) = self.loopback.take() {
            let _ = c.kill();
            let _ = c.wait();
        }
        for id in self.modules.iter().rev() {
            let _ = pactl(&["unload-module", id]);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIST: &str = "\
12\tmodule-null-sink\tsink_name=phonecam_mic_sink sink_properties=device.description='x' channels=1\t
13\tmodule-virtual-source\tsource_name=phonecam_mic master=phonecam_mic_sink.monitor\t
20\tmodule-null-sink\tsink_name=outro_sink\t
";

    #[test]
    fn acha_so_os_modulos_do_phonecam() {
        assert_eq!(
            module_ids(LIST, "module-null-sink", "sink_name", SINK_NAME),
            ["12"]
        );
        assert_eq!(
            module_ids(LIST, "module-virtual-source", "source_name", SOURCE_NAME),
            ["13"]
        );
        assert!(module_ids(LIST, "module-null-sink", "sink_name", "nada").is_empty());
    }

    #[test]
    fn nome_parecido_nao_casa() {
        let l = "7\tmodule-null-sink\tsink_name=phonecam_mic_sink_2\t\n";
        assert!(module_ids(l, "module-null-sink", "sink_name", SINK_NAME).is_empty());
    }
}
