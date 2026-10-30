"""Ways to move a SLOP packet from one place to another, and what to do on arrival."""

from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path
from typing import Any

from slop.packet import Packet

_lock = threading.Lock()


def _stamp(packet: Packet) -> str:
    now = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    crc = packet.to_bytes()[-4:].hex()
    return f"{now}-{crc}"


def store(packet: Packet, out_dir: str | Path) -> dict[str, Any]:
    """Write the packet and its plain-text description to ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with _lock:
        stem = _stamp(packet)
    packet_path = packet.save(out / f"{stem}.slop")
    text_path = out / f"{stem}.txt"
    text_path.write_text(packet.description + "\n", encoding="utf-8")
    return {
        "id": stem,
        "bytes": len(packet.to_bytes()),
        "packet": str(packet_path),
        "description": str(text_path),
    }


def receive(
    packet: Packet,
    out_dir: str | Path,
    *,
    decode: bool = True,
    backend: str = "auto",
    seed: int | None = None,
) -> dict[str, Any]:
    """Store a packet and, optionally, hallucinate it back into an image."""
    record = store(packet, out_dir)
    if not decode:
        return record
    from slop.decoder import decode_detailed

    try:
        result = decode_detailed(packet, backend=backend, seed=seed)
    except Exception as exc:  # keep the server alive; report why
        record["decode_error"] = f"{type(exc).__name__}: {exc}"
        return record
    image_path = Path(out_dir) / f"{record['id']}.png"
    result.save(image_path)
    record.update(
        {
            "image": str(image_path),
            "backend": result.backend,
            "model": result.model,
            "decode_seconds": round(result.seconds, 2),
            "decode_cost_usd_estimate": round(result.cost_usd, 4),
        }
    )
    return record
