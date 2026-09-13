"""QR code rendering (ASCII for the terminal, PNG for browsers)."""

from __future__ import annotations

import io

from phonecam.dependencies import HAS_QRCODE

if HAS_QRCODE:
    from qrcode.constants import ERROR_CORRECT_M


def qr_ascii(url: str) -> str:
    """Render a QR code as terminal-friendly unicode half-blocks."""
    if not HAS_QRCODE:
        return "[QR Code unavailable - install: pip install qrcode[pil]]"

    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    h = len(matrix)
    w = len(matrix[0]) if h else 0

    # Two module rows per text line using half-block characters.
    lines = []
    for y in range(0, h, 2):
        row = []
        for x in range(w):
            top = matrix[y][x] if y < h else False
            bot = matrix[y + 1][x] if (y + 1) < h else False
            if top and bot:
                row.append("\u2588")  # full block
            elif top:
                row.append("\u2580")  # upper half
            elif bot:
                row.append("\u2584")  # lower half
            else:
                row.append(" ")
        lines.append("".join(row))
    return "\n".join(lines)


def qr_png_bytes(url: str, size: int = 512) -> bytes:
    """Render a QR code as a PNG image."""
    if not HAS_QRCODE:
        raise RuntimeError("qrcode not installed")

    import qrcode
    from PIL import Image

    img = qrcode.make(url, box_size=10, border=2)
    img = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
