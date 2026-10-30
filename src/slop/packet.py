"""The SLOP packet: a small, honest binary container for a dishonest image.

Layout (all integers big-endian), see SPEC.md section 4::

    offset size field
    0      4    magic          b"SLOP"
    4      1    version        0x01
    5      1    flags          bits 0-1 compression, bit 2 terse, bits 3-7 reserved (0)
    6      2    width hint     pixels, 0 = receiver's choice
    8      2    height hint    pixels, 0 = receiver's choice
    10     2    body length    bytes
    12     n    body           UTF-8 description, optionally compressed
    12+n   4    checksum       CRC-32 of the body as transmitted

Text form: ``slop:`` + base85 (RFC 1924 alphabet) of the packet, or
``slop64:`` + URL-safe base64 for places where base85 punctuation is unwelcome.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import Path
from typing import Any

MAGIC = b"SLOP"
VERSION = 0x01
HEADER = struct.Struct(">4sBBHHH")
HEADER_SIZE = HEADER.size  # 12
CRC_SIZE = 4
OVERHEAD = HEADER_SIZE + CRC_SIZE  # 16
MAX_BODY = 0xFFFF
MAX_DIMENSION = 0xFFFF

FLAG_COMPRESSION_MASK = 0b0000_0011
FLAG_TERSE = 0b0000_0100
FLAG_RESERVED_MASK = 0b1111_1000

TEXT_PREFIX = "slop:"
TEXT_PREFIX_B64 = "slop64:"


class PacketError(ValueError):
    """Raised when bytes claim to be SLOP and are not."""


class Compression(IntEnum):
    NONE = 0
    ZLIB = 1
    BROTLI = 2

    @classmethod
    def parse(cls, value: str | int | Compression) -> Compression:
        if isinstance(value, Compression):
            return value
        if isinstance(value, int):
            return cls(value)
        try:
            return cls[value.upper()]
        except KeyError:
            raise ValueError(
                f"unknown compression {value!r}; expected none, zlib, brotli or auto"
            ) from None


def brotli_available() -> bool:
    try:
        import brotli  # noqa: F401
    except ImportError:
        return False
    return True


def _compress(data: bytes, method: Compression) -> bytes:
    if method is Compression.NONE:
        return data
    if method is Compression.ZLIB:
        return zlib.compress(data, 9)
    if method is Compression.BROTLI:
        try:
            import brotli
        except ImportError as exc:
            raise PacketError(
                "brotli compression needs the 'brotli' package: pip install 'slop-protocol[brotli]'"
            ) from exc
        return brotli.compress(data, quality=11)
    raise PacketError(f"unknown compression {method}")  # pragma: no cover


def _decompress(data: bytes, method: Compression) -> bytes:
    try:
        if method is Compression.NONE:
            return data
        if method is Compression.ZLIB:
            return zlib.decompress(data)
        if method is Compression.BROTLI:
            try:
                import brotli
            except ImportError as exc:
                raise PacketError(
                    "this packet is brotli-compressed; pip install 'slop-protocol[brotli]'"
                ) from exc
            return brotli.decompress(data)
    except PacketError:
        raise
    except Exception as exc:  # zlib.error, brotli.error
        raise PacketError(f"body failed to decompress ({method.name.lower()}): {exc}") from exc
    raise PacketError(f"unknown compression id {int(method)}")  # pragma: no cover


@dataclass
class Packet:
    """A description of an image, plus just enough header to be a protocol."""

    description: str
    width: int = 0
    height: int = 0
    terse: bool = False
    compression: Compression = Compression.ZLIB
    version: int = VERSION
    #: Not transmitted. Encoder bookkeeping (usage, timings) rides along here.
    meta: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        self.compression = Compression.parse(self.compression)
        for name in ("width", "height"):
            value = getattr(self, name)
            if not 0 <= value <= MAX_DIMENSION:
                raise PacketError(f"{name} hint {value} does not fit in 16 bits")

    # ----------------------------------------------------------------- encode

    @property
    def flags(self) -> int:
        return int(self.compression) | (FLAG_TERSE if self.terse else 0)

    def body(self) -> bytes:
        return _compress(self.description.encode("utf-8"), self.compression)

    def to_bytes(self) -> bytes:
        body = self.body()
        if len(body) > MAX_BODY:
            raise PacketError(
                f"body is {len(body)} bytes; SLOP v1 carries at most {MAX_BODY}. "
                "Describe less. (We cannot believe we had to write this error.)"
            )
        header = HEADER.pack(MAGIC, self.version, self.flags, self.width, self.height, len(body))
        return header + body + struct.pack(">I", zlib.crc32(body))

    def __bytes__(self) -> bytes:
        return self.to_bytes()

    def __len__(self) -> int:
        return len(self.to_bytes())

    def to_text(self, alphabet: str = "b85") -> str:
        raw = self.to_bytes()
        if alphabet == "b85":
            return TEXT_PREFIX + base64.b85encode(raw).decode("ascii")
        if alphabet == "b64":
            return TEXT_PREFIX_B64 + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        raise ValueError("alphabet must be 'b85' or 'b64'")

    def with_compression(self, compression: str | Compression) -> Packet:
        """Return a copy, choosing the smallest method when ``compression='auto'``."""
        if compression == "auto":
            return smallest(self)
        return replace(self, compression=Compression.parse(compression), meta=dict(self.meta))

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        return path

    # ----------------------------------------------------------------- decode

    @classmethod
    def from_bytes(cls, data: bytes) -> Packet:
        data = bytes(data)
        if len(data) < OVERHEAD:
            raise PacketError(f"packet is {len(data)} bytes; the header alone is {OVERHEAD}")
        magic, version, flags, width, height, length = HEADER.unpack_from(data)
        if magic != MAGIC:
            raise PacketError(f"bad magic {magic!r}; this is not SLOP (it may be something worse)")
        if version != VERSION:
            raise PacketError(f"unsupported SLOP version {version}; this receiver speaks {VERSION}")
        if flags & FLAG_RESERVED_MASK:
            raise PacketError(f"reserved flag bits set (flags=0b{flags:08b})")
        expected = HEADER_SIZE + length + CRC_SIZE
        if len(data) != expected:
            raise PacketError(
                f"length mismatch: header promises {expected} bytes, received {len(data)}"
            )
        body = data[HEADER_SIZE : HEADER_SIZE + length]
        (crc,) = struct.unpack_from(">I", data, HEADER_SIZE + length)
        actual = zlib.crc32(body)
        if crc != actual:
            raise PacketError(f"CRC mismatch: packet says {crc:08x}, body hashes to {actual:08x}")
        try:
            compression = Compression(flags & FLAG_COMPRESSION_MASK)
        except ValueError:
            raise PacketError(f"unknown compression id {flags & FLAG_COMPRESSION_MASK}") from None
        raw = _decompress(body, compression)
        try:
            description = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PacketError(f"body is not valid UTF-8: {exc}") from exc
        return cls(
            description=description,
            width=width,
            height=height,
            terse=bool(flags & FLAG_TERSE),
            compression=compression,
            version=version,
        )

    @classmethod
    def from_text(cls, text: str) -> Packet:
        text = "".join(text.split())  # tolerate wrapping from chat clients
        try:
            if text.startswith(TEXT_PREFIX_B64):
                payload = text[len(TEXT_PREFIX_B64) :]
                raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            elif text.startswith(TEXT_PREFIX):
                raw = base64.b85decode(text[len(TEXT_PREFIX) :])
            else:
                raise PacketError(
                    f"text form must start with {TEXT_PREFIX!r} or {TEXT_PREFIX_B64!r}"
                )
        except (ValueError, binascii.Error) as exc:
            if isinstance(exc, PacketError):
                raise
            raise PacketError(f"text form is not valid: {exc}") from exc
        return cls.from_bytes(raw)

    @classmethod
    def load(cls, source: str | bytes | Path | Packet) -> Packet:
        """Accept a Packet, raw bytes, a text form, or a path to either."""
        if isinstance(source, Packet):
            return source
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
            if data.startswith(MAGIC):
                return cls.from_bytes(data)
            return cls.from_text(data.decode("ascii", errors="strict"))
        if isinstance(source, str) and source.lstrip().startswith((TEXT_PREFIX, TEXT_PREFIX_B64)):
            return cls.from_text(source)
        path = Path(source)
        data = path.read_bytes()
        if data.startswith(MAGIC):
            return cls.from_bytes(data)
        return cls.from_text(data.decode("ascii"))

    # ----------------------------------------------------------------- report

    def sizes(self) -> dict[str, int]:
        """Packet size under every compression, plus the text forms."""
        out: dict[str, int] = {"description_utf8": len(self.description.encode("utf-8"))}
        methods = [Compression.NONE, Compression.ZLIB]
        if brotli_available():
            methods.append(Compression.BROTLI)
        for method in methods:
            out[method.name.lower()] = len(replace(self, compression=method).to_bytes())
        out["text_b85"] = len(self.to_text("b85"))
        out["text_b64"] = len(self.to_text("b64"))
        return out


def smallest(packet: Packet) -> Packet:
    """The same packet, re-wrapped in whichever compression is smallest."""
    methods = [Compression.NONE, Compression.ZLIB]
    if brotli_available():
        methods.append(Compression.BROTLI)
    candidates = [replace(packet, compression=m, meta=dict(packet.meta)) for m in methods]
    return min(candidates, key=lambda p: len(p.to_bytes()))


def read_header(data: bytes) -> tuple[int, int]:
    """Return (body_length, total_packet_length) from the first 12 bytes.

    Used by stream transports, since a SLOP packet is self-delimiting.
    """
    if len(data) < HEADER_SIZE:
        raise PacketError("need 12 header bytes")
    magic, version, _flags, _w, _h, length = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise PacketError(f"bad magic {magic!r}")
    if version != VERSION:
        raise PacketError(f"unsupported SLOP version {version}")
    return length, HEADER_SIZE + length + CRC_SIZE
