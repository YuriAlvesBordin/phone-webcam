//! PIN, limitador de tentativas por IP e checagem de Origin.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use rand::Rng;
use subtle::ConstantTimeEq;

use crate::config::PIN_LENGTH;

/// PIN numérico aleatório (sem zeros à esquerda).
pub fn generate_pin() -> String {
    let lower = 10u32.pow(PIN_LENGTH as u32 - 1);
    let n = rand::rng().random_range(lower..lower * 10);
    format!("{n:0width$}", width = PIN_LENGTH)
}

/// Token do QR: 128 bits aleatórios em hexadecimal. Vai no fragmento da URL (`#k=`), que o
/// navegador nunca envia ao servidor; só quem viu o QR na tela do PC o tem.
pub fn generate_token() -> String {
    let n: u128 = rand::rng().random();
    format!("{n:032x}")
}

/// Comparação em tempo constante.
pub fn pin_matches(guess: &str, actual: &str) -> bool {
    guess.len() == actual.len() && bool::from(guess.as_bytes().ct_eq(actual.as_bytes()))
}

/// Falhas por chave (IP) com bloqueio temporário.
pub struct RateLimiter {
    max_failures: u32,
    lockout: Duration,
    failures: HashMap<String, u32>,
    locked_until: HashMap<String, Instant>,
}

impl RateLimiter {
    pub fn new(max_failures: u32, lockout: Duration) -> Self {
        Self {
            max_failures,
            lockout,
            failures: HashMap::new(),
            locked_until: HashMap::new(),
        }
    }

    /// Quanto falta de bloqueio para a chave (`None` se livre).
    pub fn lockout_remaining(&mut self, key: &str, now: Instant) -> Option<Duration> {
        match self.locked_until.get(key) {
            Some(&until) if until > now => Some(until - now),
            Some(_) => {
                self.locked_until.remove(key);
                None
            }
            None => None,
        }
    }

    pub fn record_failure(&mut self, key: &str, now: Instant) {
        let count = self.failures.entry(key.to_string()).or_insert(0);
        *count += 1;
        if *count >= self.max_failures {
            self.locked_until
                .insert(key.to_string(), now + self.lockout);
            self.failures.remove(key);
        }
    }

    pub fn reset(&mut self, key: &str) {
        self.failures.remove(key);
        self.locked_until.remove(key);
    }
}

/// Aceita conexões de navegador só do nosso próprio host. Sem Origin é cliente
/// que não é navegador (curl, testes): liberado. O host (sem porta) precisa
/// bater com o do cabeçalho Host.
pub fn origin_allowed(origin: Option<&str>, host_header: Option<&str>) -> bool {
    let Some(origin) = origin.filter(|o| !o.is_empty()) else {
        return true;
    };
    match (
        origin_host(origin),
        host_of_header(host_header.unwrap_or("")),
    ) {
        (Some(o), Some(h)) => o.eq_ignore_ascii_case(&h),
        _ => false,
    }
}

fn origin_host(origin: &str) -> Option<String> {
    let rest = origin.split_once("://")?.1;
    host_of_header(rest.split('/').next()?)
}

/// `host`, `host:porta` ou `[::1]:porta` -> host sem porta.
fn host_of_header(h: &str) -> Option<String> {
    let h = h.trim();
    if let Some(rest) = h.strip_prefix('[') {
        return Some(rest.split(']').next()?.to_string()).filter(|s| !s.is_empty());
    }
    let host = h.rsplit_once(':').map_or(h, |(host, port)| {
        if port.bytes().all(|b| b.is_ascii_digit()) {
            host
        } else {
            h
        }
    });
    Some(host.to_string()).filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pin_tem_seis_digitos() {
        for _ in 0..200 {
            let p = generate_pin();
            assert_eq!(p.len(), PIN_LENGTH);
            assert!(p.bytes().all(|b| b.is_ascii_digit()));
            assert!(!p.starts_with('0'));
        }
    }

    #[test]
    fn token_tem_128_bits_e_nao_repete() {
        let (a, b) = (generate_token(), generate_token());
        assert_eq!(a.len(), 32);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn comparacao_do_pin() {
        assert!(pin_matches("123456", "123456"));
        assert!(!pin_matches("123457", "123456"));
        assert!(!pin_matches("12345", "123456"));
        assert!(!pin_matches("", "123456"));
    }

    #[test]
    fn bloqueia_depois_de_cinco_falhas_e_libera_com_o_tempo() {
        let mut rl = RateLimiter::new(5, Duration::from_secs(60));
        let t0 = Instant::now();
        for _ in 0..4 {
            rl.record_failure("ip", t0);
        }
        assert!(rl.lockout_remaining("ip", t0).is_none());
        rl.record_failure("ip", t0);
        assert!(rl.lockout_remaining("ip", t0).is_some());
        assert!(rl.lockout_remaining("outro", t0).is_none());
        assert!(rl
            .lockout_remaining("ip", t0 + Duration::from_secs(61))
            .is_none());
    }

    #[test]
    fn reset_limpa_o_bloqueio() {
        let mut rl = RateLimiter::new(1, Duration::from_secs(60));
        let t0 = Instant::now();
        rl.record_failure("ip", t0);
        assert!(rl.lockout_remaining("ip", t0).is_some());
        rl.reset("ip");
        assert!(rl.lockout_remaining("ip", t0).is_none());
    }

    #[test]
    fn origin() {
        assert!(origin_allowed(None, Some("192.168.3.13:8765")));
        assert!(origin_allowed(Some(""), None));
        assert!(origin_allowed(
            Some("https://192.168.3.13:8765"),
            Some("192.168.3.13:8765")
        ));
        assert!(!origin_allowed(
            Some("https://evil.example"),
            Some("192.168.3.13:8765")
        ));
        assert!(origin_allowed(
            Some("https://[::1]:8765"),
            Some("[::1]:8765")
        ));
        assert!(!origin_allowed(Some("lixo"), Some("a:1")));
        assert!(!origin_allowed(Some("https://a"), None));
    }
}
