#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("E/S: {0}")]
    Io(#[from] std::io::Error),
    #[error("certificado TLS: {0}")]
    Tls(String),
    #[error("GStreamer: {0}")]
    Gst(String),
    #[error("configuração: {0}")]
    Config(String),
    #[error("{0}")]
    Other(String),
}

impl From<gstreamer::glib::Error> for Error {
    fn from(e: gstreamer::glib::Error) -> Self {
        Error::Gst(e.to_string())
    }
}

impl From<gstreamer::glib::BoolError> for Error {
    fn from(e: gstreamer::glib::BoolError) -> Self {
        Error::Gst(e.to_string())
    }
}

impl From<gstreamer::StateChangeError> for Error {
    fn from(e: gstreamer::StateChangeError) -> Self {
        Error::Gst(e.to_string())
    }
}
