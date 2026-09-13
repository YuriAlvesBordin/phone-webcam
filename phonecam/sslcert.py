"""Self-signed certificate generation for local HTTPS."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import subprocess
from pathlib import Path

from phonecam.config import CERT_KEY_SIZE, CERT_VALIDITY_DAYS
from phonecam.net import get_local_ips

log = logging.getLogger(__name__)


def ensure_ssl_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Return (cert_file, key_file), generating a self-signed pair on first
    use. Prefers the ``cryptography`` package, falls back to the openssl CLI."""
    cert_file = cert_dir / "phonecam.pem"
    key_file = cert_dir / "phonecam.key"
    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    cert_dir.mkdir(parents=True, exist_ok=True)

    # Local IPs go into the SAN so browsers accept the cert per IP.
    san_ips = [ipaddress.ip_address("127.0.0.1")]
    for ip in get_local_ips():
        try:
            san_ips.append(ipaddress.ip_address(ip))
        except ValueError:
            continue

    try:
        _generate_with_cryptography(cert_file, key_file, san_ips)
    except ImportError:
        log.info("cryptography not installed - falling back to openssl CLI")
        _generate_with_openssl(cert_file, key_file)

    log.info("SSL certificate ready: %s", cert_file)
    return cert_file, key_file


def _generate_with_cryptography(
    cert_file: Path, key_file: Path, san_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address]
) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    log.info("Generating self-signed SSL certificate (cryptography)...")
    key = rsa.generate_private_key(public_exponent=65537, key_size=CERT_KEY_SIZE)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "PhoneCam"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PhoneCam Local"),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("PhoneCam")] + [x509.IPAddress(ip) for ip in san_ips]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _generate_with_openssl(cert_file: Path, key_file: Path) -> None:
    log.info("Generating self-signed SSL certificate (openssl)...")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            f"rsa:{CERT_KEY_SIZE}",
            "-keyout",
            str(key_file),
            "-out",
            str(cert_file),
            "-days",
            str(CERT_VALIDITY_DAYS),
            "-nodes",
            "-subj",
            "/CN=PhoneCam/O=PhoneCam Local",
        ],
        check=True,
        capture_output=True,
    )
