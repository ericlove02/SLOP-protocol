"""Receiver: SLOP packet -> prompt -> an image (not the image)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from slop import config, prompts
from slop.packet import Packet
from slop.providers import ImageGenProvider, Usage, get_imagegen

PacketSource = Packet | bytes | str | Path


@dataclass
class DecodeResult:
    image: Image.Image
    packet: Packet
    prompt: str
    backend: str
    model: str
    usage: list[Usage] = field(default_factory=list)
    seconds: float = 0.0
    seed: int | None = None
    seed_honoured: bool = False

    @property
    def cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.usage)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path)
        return path


def output_size(packet: Packet, size: tuple[int, int] | None = None) -> tuple[int, int]:
    if size is not None:
        return size
    if packet.width and packet.height:
        return packet.width, packet.height
    return config.DEFAULT_OUTPUT_SIZE


def decode_detailed(
    source: PacketSource,
    *,
    backend: str | ImageGenProvider = "auto",
    model: str | None = None,
    seed: int | None = None,
    size: tuple[int, int] | None = None,
) -> DecodeResult:
    """Validate a packet and ask an image model to imagine what it describes."""
    config.load_env()
    packet = Packet.load(source)
    provider = (
        backend
        if not isinstance(backend, str)
        else get_imagegen(backend, **({"model": model} if model else {}))
    )
    prompt = prompts.generation_prompt(packet.description, provider.name, packet.terse)
    target = output_size(packet, size)
    start = time.perf_counter()
    image = provider.generate(prompt, target, seed)
    elapsed = time.perf_counter() - start
    usage = [provider.last_usage] if provider.last_usage is not None else []
    return DecodeResult(
        image=image,
        packet=packet,
        prompt=prompt,
        backend=provider.name,
        model=provider.model,
        usage=usage,
        seconds=elapsed,
        seed=seed,
        seed_honoured=seed is not None and provider.supports_seed,
    )


def decode(source: PacketSource, **options: Any) -> Image.Image:
    """``slop.decode(packet) -> PIL.Image``. See ``decode_detailed`` for options."""
    return decode_detailed(source, **options).image
