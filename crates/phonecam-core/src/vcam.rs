//! Detecção da webcam virtual (v4l2loopback).

use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Device {
    pub path: String,
    pub name: String,
}

/// Dispositivos v4l2loopback: os que ficam em `/sys/devices/virtual`.
fn scan(sys_class: &Path) -> Vec<Device> {
    let Ok(rd) = std::fs::read_dir(sys_class) else {
        return Vec::new();
    };
    let mut out: Vec<Device> = rd
        .flatten()
        .filter_map(|e| {
            let fname = e.file_name().to_string_lossy().into_owned();
            let virt = std::fs::canonicalize(e.path())
                .map(|p| p.to_string_lossy().contains("/virtual/"))
                .unwrap_or(false);
            if !fname.starts_with("video") || !virt {
                return None;
            }
            let name = std::fs::read_to_string(e.path().join("name"))
                .unwrap_or_default()
                .trim()
                .to_string();
            Some(Device {
                path: format!("/dev/{fname}"),
                name,
            })
        })
        .collect();
    out.sort_by(|a, b| a.path.len().cmp(&b.path.len()).then(a.path.cmp(&b.path)));
    out
}

/// Escolhe o dispositivo: o pedido, senão o de nome "PhoneCam", senão o primeiro virtual.
pub fn pick(devices: &[Device], wanted: Option<&str>) -> Option<Device> {
    if let Some(w) = wanted {
        return devices.iter().find(|d| d.path == w).cloned().or_else(|| {
            Path::new(w).exists().then(|| Device {
                path: w.to_string(),
                name: String::new(),
            })
        });
    }
    devices
        .iter()
        .find(|d| d.name.eq_ignore_ascii_case("PhoneCam"))
        .or_else(|| devices.first())
        .cloned()
}

pub fn find(wanted: Option<&str>) -> Option<Device> {
    pick(&scan(Path::new("/sys/class/video4linux")), wanted)
}

/// `exclusive_caps` ligado em todos os dispositivos? (Discord/Chrome só listam assim.)
pub fn exclusive_caps_ok() -> Option<bool> {
    let text =
        std::fs::read_to_string("/sys/module/v4l2loopback/parameters/exclusive_caps").ok()?;
    Some(text.trim().split(',').all(|v| matches!(v, "Y" | "y" | "1")))
}

/// O módulo está carregado?
pub fn module_loaded() -> bool {
    Path::new("/sys/module/v4l2loopback").exists()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(path: &str, name: &str) -> Device {
        Device {
            path: path.into(),
            name: name.into(),
        }
    }

    #[test]
    fn prefere_o_phonecam() {
        let devs = [d("/dev/video2", "Dummy"), d("/dev/video10", "PhoneCam")];
        assert_eq!(pick(&devs, None).unwrap().path, "/dev/video10");
    }

    #[test]
    fn sem_nome_conhecido_pega_o_primeiro() {
        let devs = [d("/dev/video2", "Dummy"), d("/dev/video3", "Outro")];
        assert_eq!(pick(&devs, None).unwrap().path, "/dev/video2");
        assert!(pick(&[], None).is_none());
    }

    #[test]
    fn dispositivo_pedido_vence() {
        let devs = [d("/dev/video10", "PhoneCam"), d("/dev/video11", "X")];
        assert_eq!(pick(&devs, Some("/dev/video11")).unwrap().name, "X");
        assert!(pick(&devs, Some("/dev/nao-existe-xyz")).is_none());
    }
}
