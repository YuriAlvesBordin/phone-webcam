//! QR code da URL de conexão (SVG para a web, texto para o terminal).

use qrcode::render::{svg, unicode};
use qrcode::QrCode;

pub fn svg(data: &str) -> Option<String> {
    let code = QrCode::new(data.as_bytes()).ok()?;
    Some(
        code.render::<svg::Color>()
            .min_dimensions(256, 256)
            .quiet_zone(true)
            .build(),
    )
}

/// Blocos Unicode (dois módulos por caractere), com fundo claro para escanear em tema escuro.
pub fn terminal(data: &str) -> Option<String> {
    let code = QrCode::new(data.as_bytes()).ok()?;
    Some(
        code.render::<unicode::Dense1x2>()
            .dark_color(unicode::Dense1x2::Light)
            .light_color(unicode::Dense1x2::Dark)
            .quiet_zone(true)
            .build(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gera_svg_e_texto() {
        let url = "https://192.168.3.13:8765/";
        assert!(svg(url).unwrap().contains("<svg"));
        assert!(terminal(url).unwrap().lines().count() > 10);
    }
}
