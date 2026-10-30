import struct
import zlib

import pytest

from slop.packet import (
    MAX_BODY,
    OVERHEAD,
    Compression,
    Packet,
    PacketError,
    brotli_available,
    read_header,
    smallest,
)

DESC = (
    "Scene: night sky, swirling cobalt (#1F3A93) clouds over sleeping village; "
    "cypress (#1B2A1F) left third, flame-shaped; crescent moon top right, #F2C94C."
)

methods = [Compression.NONE, Compression.ZLIB]
if brotli_available():
    methods.append(Compression.BROTLI)


@pytest.mark.parametrize("method", methods)
def test_round_trip(method):
    p = Packet(DESC, width=1024, height=768, terse=True, compression=method)
    q = Packet.from_bytes(p.to_bytes())
    assert q == p
    assert q.description == DESC
    assert (q.width, q.height, q.terse, q.compression) == (1024, 768, True, method)


def test_header_layout_is_exactly_as_specified():
    p = Packet("hi", width=3, height=4, compression=Compression.NONE)
    raw = p.to_bytes()
    assert raw[:4] == b"SLOP"
    assert raw[4] == 1
    assert raw[5] == 0
    assert struct.unpack(">HHH", raw[6:12]) == (3, 4, 2)
    assert raw[12:14] == b"hi"
    assert struct.unpack(">I", raw[14:]) == (zlib.crc32(b"hi"),)
    assert len(raw) == OVERHEAD + 2


def test_terse_flag_bit():
    raw = Packet("x", terse=True, compression=Compression.ZLIB).to_bytes()
    assert raw[5] == 0b101


def test_unicode_round_trip():
    p = Packet("crème brûlée, 🍮, 東京 at dusk")
    assert Packet.from_bytes(p.to_bytes()).description == p.description


def test_bad_magic():
    raw = bytearray(Packet(DESC).to_bytes())
    raw[:4] = b"PNG!"
    with pytest.raises(PacketError, match="bad magic"):
        Packet.from_bytes(bytes(raw))


def test_bad_version():
    raw = bytearray(Packet(DESC).to_bytes())
    raw[4] = 2
    with pytest.raises(PacketError, match="version"):
        Packet.from_bytes(bytes(raw))


def test_bad_crc():
    raw = bytearray(Packet(DESC, compression=Compression.NONE).to_bytes())
    raw[20] ^= 0xFF
    with pytest.raises(PacketError, match="CRC"):
        Packet.from_bytes(bytes(raw))


def test_reserved_flags_rejected():
    raw = bytearray(Packet(DESC).to_bytes())
    raw[5] |= 0b1000_0000
    with pytest.raises(PacketError, match="reserved"):
        Packet.from_bytes(bytes(raw))


def test_unknown_compression_rejected():
    body = b"hello"
    header = struct.pack(">4sBBHHH", b"SLOP", 1, 3, 0, 0, len(body))
    raw = header + body + struct.pack(">I", zlib.crc32(body))
    with pytest.raises(PacketError, match="compression"):
        Packet.from_bytes(raw)


def test_truncated_and_padded():
    raw = Packet(DESC).to_bytes()
    with pytest.raises(PacketError, match="length mismatch"):
        Packet.from_bytes(raw[:-1])
    with pytest.raises(PacketError, match="length mismatch"):
        Packet.from_bytes(raw + b"\0")
    with pytest.raises(PacketError, match="header alone"):
        Packet.from_bytes(b"SLOP")


def test_corrupt_compressed_body():
    body = b"definitely not zlib"
    header = struct.pack(">4sBBHHH", b"SLOP", 1, 1, 0, 0, len(body))
    raw = header + body + struct.pack(">I", zlib.crc32(body))
    with pytest.raises(PacketError, match="decompress"):
        Packet.from_bytes(raw)


def test_oversize_body():
    import os

    noise = os.urandom(MAX_BODY).hex()  # incompressible enough
    with pytest.raises(PacketError, match="at most"):
        Packet(noise, compression=Compression.NONE).to_bytes()


def test_oversize_dimension():
    with pytest.raises(PacketError, match="16 bits"):
        Packet("x", width=70_000)


@pytest.mark.parametrize("alphabet", ["b85", "b64"])
def test_text_round_trip(alphabet):
    p = Packet(DESC, width=512, height=512)
    text = p.to_text(alphabet)
    assert text.startswith("slop:" if alphabet == "b85" else "slop64:")
    assert Packet.from_text(text) == p
    # chat clients love to insert line breaks
    wrapped = "\n".join(text[i : i + 40] for i in range(0, len(text), 40))
    assert Packet.from_text(wrapped) == p


def test_text_rejects_garbage():
    with pytest.raises(PacketError):
        Packet.from_text("hello world")
    with pytest.raises(PacketError):
        Packet.from_text("slop:~~~~")


def test_load_accepts_everything(tmp_path):
    p = Packet(DESC)
    path = p.save(tmp_path / "a.slop")
    (tmp_path / "a.txt").write_text(p.to_text())
    assert Packet.load(path) == p
    assert Packet.load(str(path)) == p
    assert Packet.load(tmp_path / "a.txt") == p
    assert Packet.load(p.to_bytes()) == p
    assert Packet.load(p.to_text()) == p
    assert Packet.load(p) is p


def test_smallest_and_sizes():
    p = Packet(DESC * 3, compression=Compression.NONE)
    best = smallest(p)
    assert len(best.to_bytes()) <= len(p.to_bytes())
    assert best.description == p.description
    sizes = p.sizes()
    assert sizes["none"] == len(DESC * 3) + OVERHEAD
    assert sizes["text_b85"] > sizes["none"]
    assert p.with_compression("auto") == best


def test_read_header():
    raw = Packet(DESC).to_bytes()
    body_len, total = read_header(raw[:12])
    assert total == len(raw)
    assert body_len == len(raw) - OVERHEAD
    with pytest.raises(PacketError):
        read_header(b"NOPE" + raw[4:12])
