//! Certificado autoassinado para o HTTPS local (celular exige contexto seguro
//! para liberar a câmera).

use std::net::IpAddr;
use std::path::{Path, PathBuf};

use rcgen::{CertificateParams, DistinguishedName, DnType, KeyPair, SanType};

use crate::error::Error;

const VALID_DAYS: i64 = 365;

pub struct CertFiles {
    pub cert: PathBuf,
    pub key: PathBuf,
}

/// Lista de IPs gravada junto do certificado: se a rede mudar, ele é refeito.
fn ips_file(dir: &Path) -> PathBuf {
    dir.join("phonecam.ips")
}

fn ips_text(ips: &[IpAddr]) -> String {
    let mut v: Vec<String> = ips.iter().map(ToString::to_string).collect();
    v.sort();
    v.join("\n")
}

/// Gera (ou reaproveita) o par cert/chave em `dir` para os IPs dados.
pub fn ensure_cert(dir: &Path, ips: &[IpAddr]) -> Result<CertFiles, Error> {
    let files = CertFiles {
        cert: dir.join("phonecam.pem"),
        key: dir.join("phonecam.key"),
    };
    let wanted = ips_text(ips);
    let current = std::fs::read_to_string(ips_file(dir)).unwrap_or_default();
    if files.cert.exists() && files.key.exists() && current == wanted {
        return Ok(files);
    }

    std::fs::create_dir_all(dir)?;
    let (cert_pem, key_pem) = generate(ips)?;
    std::fs::write(&files.cert, cert_pem)?;
    write_private(&files.key, key_pem.as_bytes())?;
    std::fs::write(ips_file(dir), wanted)?;
    tracing::info!("certificado autoassinado gerado em {}", dir.display());
    Ok(files)
}

fn write_private(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)?;
    f.write_all(bytes)
}

pub fn generate(ips: &[IpAddr]) -> Result<(String, String), Error> {
    let mut params = CertificateParams::default();
    let mut dn = DistinguishedName::new();
    dn.push(DnType::CommonName, "PhoneCam");
    dn.push(DnType::OrganizationName, "PhoneCam Local");
    params.distinguished_name = dn;
    params.subject_alt_names = vec![
        SanType::DnsName(
            "PhoneCam"
                .try_into()
                .map_err(|_| Error::Tls("SAN".into()))?,
        ),
        SanType::DnsName(
            "localhost"
                .try_into()
                .map_err(|_| Error::Tls("SAN".into()))?,
        ),
        SanType::IpAddress(IpAddr::from([127, 0, 0, 1])),
    ];
    params
        .subject_alt_names
        .extend(ips.iter().copied().map(SanType::IpAddress));
    let start = time::OffsetDateTime::now_utc();
    params.not_before = start;
    params.not_after = start + time::Duration::days(VALID_DAYS);

    let key = KeyPair::generate().map_err(|e| Error::Tls(e.to_string()))?;
    let cert = params
        .self_signed(&key)
        .map_err(|e| Error::Tls(e.to_string()))?;
    Ok((cert.pem(), key.serialize_pem()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gera_pem_valido() {
        let ips = ["192.168.3.13".parse().unwrap()];
        let (cert, key) = generate(&ips).unwrap();
        assert!(cert.contains("BEGIN CERTIFICATE"));
        assert!(key.contains("PRIVATE KEY"));
    }

    #[test]
    fn reaproveita_enquanto_os_ips_nao_mudam() {
        let dir = std::env::temp_dir().join(format!("phonecam-tls-{}", std::process::id()));
        let a: Vec<IpAddr> = vec!["10.0.0.2".parse().unwrap()];
        let f1 = ensure_cert(&dir, &a).unwrap();
        let c1 = std::fs::read(&f1.cert).unwrap();
        let f2 = ensure_cert(&dir, &a).unwrap();
        assert_eq!(c1, std::fs::read(&f2.cert).unwrap());
        let b: Vec<IpAddr> = vec!["10.0.0.3".parse().unwrap()];
        ensure_cert(&dir, &b).unwrap();
        assert_ne!(c1, std::fs::read(&f1.cert).unwrap());
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
