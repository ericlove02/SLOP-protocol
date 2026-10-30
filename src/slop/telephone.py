"""The telephone game: encode, decode, encode the result, decode that...

Each round is a fresh lossy trip through words. Watch the meaning drift.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from slop.decoder import DecodeResult, decode_detailed
from slop.encoder import EncodeResult, ImageSource, encode_detailed, load_image
from slop.imaging import filmstrip
from slop.providers import ImageGenProvider, VisionProvider, get_imagegen, get_vision


@dataclass
class Round:
    index: int
    encoded: EncodeResult
    decoded: DecodeResult

    @property
    def image(self) -> Image.Image:
        return self.decoded.image

    @property
    def description(self) -> str:
        return self.encoded.packet.description


def play(
    source: ImageSource,
    rounds: int = 10,
    *,
    vision: str | VisionProvider = "auto",
    backend: str | ImageGenProvider = "auto",
    terse: bool = False,
    max_bytes: int | None = None,
    seed: int | None = None,
    out_dir: str | Path | None = None,
    on_round: Callable[[Round], None] | None = None,
) -> list[Round]:
    """Run ``rounds`` encode->decode trips, each starting from the previous output."""
    vision_p = get_vision(vision) if isinstance(vision, str) else vision
    backend_p = get_imagegen(backend) if isinstance(backend, str) else backend
    current, _ = load_image(source)
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        current.convert("RGB").save(out / "gen00_original.png")
    history: list[Round] = []
    for i in range(1, rounds + 1):
        encoded = encode_detailed(current, vision=vision_p, terse=terse, max_bytes=max_bytes)
        decoded = decode_detailed(encoded.packet, backend=backend_p, seed=seed)
        rnd = Round(i, encoded, decoded)
        history.append(rnd)
        if out:
            decoded.save(out / f"gen{i:02d}.png")
            encoded.packet.save(out / f"gen{i:02d}.slop")
            (out / f"gen{i:02d}.txt").write_text(encoded.packet.description + "\n")
        if on_round:
            on_round(rnd)
        current = decoded.image
    return history


def strip(original: Image.Image, history: list[Round], caption: str | None = None) -> Image.Image:
    images = [original] + [r.image for r in history]
    labels = ["original"] + [
        f"gen {r.index}\n{len(r.encoded.packet.to_bytes())} B" for r in history
    ]
    return filmstrip(images, labels, caption=caption, per_row=min(6, len(images)))
