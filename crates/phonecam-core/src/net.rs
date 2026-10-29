//! Endereços locais para a URL do QR e para o certificado.

use std::net::IpAddr;

/// Interfaces de contêiner/VM/VPN que não servem para o celular alcançar o PC.
const IGNORED_PREFIXES: [&str; 7] = ["docker", "br-", "veth", "virbr", "vmnet", "lxc", "podman"];

fn private_v4(ip: &std::net::Ipv4Addr) -> bool {
    ip.is_private()
}

/// IPv4 da LAN, privados primeiro (192.168.x antes de 10.x antes de 172.16.x).
pub fn local_ips() -> Vec<IpAddr> {
    let Ok(addrs) = if_addrs::get_if_addrs() else {
        return Vec::new();
    };
    let mut ips: Vec<(u8, IpAddr)> = addrs
        .into_iter()
        .filter(|i| !i.is_loopback())
        .filter(|i| !IGNORED_PREFIXES.iter().any(|p| i.name.starts_with(p)))
        .filter_map(|i| match i.ip() {
            IpAddr::V4(v4) if !v4.is_link_local() => {
                let rank = match v4.octets() {
                    [192, 168, ..] => 0,
                    [10, ..] => 1,
                    _ if private_v4(&v4) => 2,
                    _ => 3,
                };
                Some((rank, IpAddr::V4(v4)))
            }
            _ => None,
        })
        .collect();
    ips.sort_by_key(|(rank, _)| *rank);
    ips.dedup();
    ips.into_iter().map(|(_, ip)| ip).collect()
}

/// URL de conexão mostrada no QR.
/// `token` vai no fragmento (`#k=`): o navegador não o envia ao servidor nem em Referer.
pub fn connection_url(https: bool, host: Option<IpAddr>, port: u16, token: Option<&str>) -> String {
    let scheme = if https { "https" } else { "http" };
    let host = host.map_or_else(|| "localhost".to_string(), |h| h.to_string());
    let mut url = format!("{scheme}://{host}:{port}/");
    if let Some(token) = token {
        url.push_str("#k=");
        url.push_str(token);
    }
    url
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url_com_e_sem_token() {
        let ip: IpAddr = "192.168.3.13".parse().unwrap();
        assert_eq!(
            connection_url(true, Some(ip), 8765, None),
            "https://192.168.3.13:8765/"
        );
        assert_eq!(
            connection_url(false, None, 80, Some("abc123")),
            "http://localhost:80/#k=abc123"
        );
    }
}
