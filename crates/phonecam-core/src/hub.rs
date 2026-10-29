//! Estado compartilhado: quem muda (servidor/sessão) e quem observa (D-Bus, CLI).

use std::sync::Arc;

use tokio::sync::watch;

use crate::status::Status;

#[derive(Clone)]
pub struct Hub(Arc<watch::Sender<Status>>);

impl Default for Hub {
    fn default() -> Self {
        Self::new()
    }
}

impl Hub {
    pub fn new() -> Self {
        Self(Arc::new(watch::channel(Status::default()).0))
    }

    /// Aplica a mudança e só notifica os observadores se algo mudou de fato.
    pub fn update(&self, f: impl FnOnce(&mut Status)) {
        self.0.send_if_modified(|s| {
            let before = s.clone();
            f(s);
            *s != before
        });
    }

    pub fn snapshot(&self) -> Status {
        self.0.borrow().clone()
    }

    pub fn subscribe(&self) -> watch::Receiver<Status> {
        self.0.subscribe()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::status::Phase;

    #[tokio::test]
    async fn so_notifica_quando_muda() {
        let hub = Hub::new();
        let mut rx = hub.subscribe();
        hub.update(|s| s.phase = Phase::Off); // sem mudança
        assert!(!rx.has_changed().unwrap());
        hub.update(|s| s.phase = Phase::Waiting);
        rx.changed().await.unwrap();
        assert_eq!(rx.borrow().phase, Phase::Waiting);
        assert_eq!(hub.snapshot().phase, Phase::Waiting);
    }
}
