"""SLOP over QR code: the image travels as a picture of its own description.

Yes, the QR code is itself an image. Yes, it is usually bigger than the
original. We are aware.
"""

from __future__ import annotations

from PIL import Image

from slop.packet import Packet, PacketError
from slop.providers.base import require

#: Byte-mode capacity of a version-40 QR code, by error-correction level.
CAPACITY = {"L": 2953, "M": 2331, "Q": 1663, "H": 1273}


def packet_to_qr(packet: Packet, *, box_size: int = 8, border: int = 4) -> Image.Image:
    qrcode = require("qrcode", "qr")
    text = packet.to_text()
    level = next((lvl for lvl in ("M", "L") if len(text) <= CAPACITY[lvl]), None)
    if level is None:
        raise PacketError(
            f"text form is {len(text)} characters; a QR code holds at most {CAPACITY['L']}. "
            "Re-encode with --max-bytes 2000 or less."
        )
    qr = qrcode.QRCode(
        error_correction=getattr(qrcode.constants, f"ERROR_CORRECT_{level}"),
        box_size=box_size,
        border=border,
    )
    qr.add_data(text, optimize=0)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


def qr_to_packet(image: Image.Image) -> Packet:
    zxingcpp = require("zxingcpp", "qr")
    results = zxingcpp.read_barcodes(image.convert("RGB"))
    for result in results:
        text = result.text
        if text.startswith(("slop:", "slop64:")):
            return Packet.from_text(text)
    if results:
        raise PacketError("found a QR code, but it does not contain a SLOP packet")
    raise PacketError("no QR code found in image")
