"""Transmitter: image -> description -> SLOP packet."""

from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from slop import config, prompts
from slop.packet import MAX_DIMENSION, OVERHEAD, TEXT_PREFIX, Compression, Packet, smallest
from slop.providers import Usage, VisionProvider, get_vision
from slop.providers.base import prepare

ImageSource = str | Path | bytes | Image.Image


class BudgetError(ValueError):
    """The byte budget is too small to hold even an empty packet."""


@dataclass
class EncodeResult:
    packet: Packet
    usage: list[Usage] = field(default_factory=list)
    seconds: float = 0.0
    attempts: int = 1
    truncated: bool = False
    original_bytes: int | None = None
    budget_bytes: int | None = None

    @property
    def cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.usage)

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.usage)

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.usage)


def load_image(source: ImageSource) -> tuple[Image.Image, int | None]:
    """Open PNG/JPEG/WebP (anything Pillow reads). Returns (image, file size)."""
    if isinstance(source, Image.Image):
        return source, None
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    image = Image.open(io.BytesIO(data))
    image.load()
    return image, len(data)


def packet_limit(max_bytes: int | None = None, max_chars: int | None = None) -> int | None:
    """Combine a binary budget and a text-form budget into one packet-byte limit."""
    limits = []
    if max_bytes is not None:
        limits.append(max_bytes)
    if max_chars is not None:
        # base85 turns 4 bytes into 5 characters, plus the "slop:" prefix
        limits.append(math.floor((max_chars - len(TEXT_PREFIX)) * 4 / 5))
    if not limits:
        return None
    limit = min(limits)
    if limit <= OVERHEAD:
        raise BudgetError(
            f"a {limit}-byte budget leaves no room for a description; "
            f"the header and checksum alone are {OVERHEAD} bytes"
        )
    return limit


def _wrap(description: str, terse: bool, size: tuple[int, int], compression: str) -> Packet:
    packet = Packet(description, width=size[0], height=size[1], terse=terse)
    if compression == "auto":
        return smallest(packet)
    return Packet(
        description,
        width=size[0],
        height=size[1],
        terse=terse,
        compression=Compression.parse(compression),
    )


def truncate_to_fit(
    description: str, limit: int, terse: bool, size: tuple[int, int], compression: str
) -> Packet:
    """Cut the description at a word boundary until the packet fits ``limit`` bytes."""
    words = description.split(" ")
    lo, hi = 0, len(words)
    best = _wrap("", terse, size, compression)
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _wrap(" ".join(words[:mid]).rstrip(" ,;:"), terse, size, compression)
        if len(candidate.to_bytes()) <= limit:
            best, lo = candidate, mid + 1
        else:
            hi = mid - 1
    if len(best.description) == 0 and words:
        # A single word longer than the budget: cut characters instead.
        text = words[0]
        while text and len(_wrap(text, terse, size, compression).to_bytes()) > limit:
            text = text[:-1]
        best = _wrap(text, terse, size, compression)
    return best


def _size_hint(image: Image.Image, size_hint: tuple[int, int] | None) -> tuple[int, int]:
    if size_hint is not None:
        w, h = size_hint
    else:
        w, h = image.size
    scale = min(1.0, MAX_DIMENSION / max(w, h, 1))
    return int(w * scale), int(h * scale)


def encode_detailed(
    source: ImageSource,
    *,
    vision: str | VisionProvider = "auto",
    model: str | None = None,
    terse: bool = False,
    max_bytes: int | None = None,
    max_chars: int | None = None,
    compression: str = "auto",
    size_hint: tuple[int, int] | None = None,
    retries: int = config.BUDGET_RETRIES,
) -> EncodeResult:
    """Describe an image and wrap the description in a SLOP packet.

    ``max_bytes`` limits the binary packet; ``max_chars`` limits the ``slop:``
    text form. If the model overshoots, it is asked to try again (``retries``
    times), and then the description is truncated at a word boundary.
    """
    config.load_env()
    image, original_bytes = load_image(source)
    size = _size_hint(image, size_hint)
    provider = (
        vision
        if not isinstance(vision, str)
        else get_vision(vision, **({"model": model} if model else {}))
    )
    language = "terse" if terse else "english"
    system = prompts.system_prompt(language)
    limit = packet_limit(max_bytes, max_chars)
    target_chars = None if limit is None else limit - OVERHEAD
    prepared = prepare(image)

    usages: list[Usage] = []
    history: list[tuple[str, str]] = []
    start = time.perf_counter()
    attempts = 0
    packet: Packet | None = None
    truncated = False
    description = ""

    for attempt in range(1 + (retries if limit is not None else 0)):
        attempts = attempt + 1
        description = provider.describe(
            prepared,
            system,
            target_chars,
            instruction=prompts.describe_instruction(target_chars),
            history=history,
        )
        if provider.last_usage is not None:
            usages.append(provider.last_usage)
        packet = _wrap(description, terse, size, compression)
        if limit is None or len(packet.to_bytes()) <= limit:
            break
        actual = len(description.encode("utf-8"))
        # Models undercount; ask for progressively less than the real limit.
        assert target_chars is not None
        ask = max(1, int(target_chars * 0.85**attempts))
        history.append((description, prompts.retry_instruction(description, actual, ask)))
    else:
        assert limit is not None
        packet = truncate_to_fit(description, limit, terse, size, compression)
        truncated = True

    assert packet is not None
    elapsed = time.perf_counter() - start
    result = EncodeResult(
        packet=packet,
        usage=usages,
        seconds=elapsed,
        attempts=attempts,
        truncated=truncated,
        original_bytes=original_bytes,
        budget_bytes=limit,
    )
    packet.meta.update(_meta(result, provider, language))
    return result


def _meta(result: EncodeResult, provider: VisionProvider, language: str) -> dict[str, Any]:
    return {
        "encoder": {"provider": provider.name, "model": provider.model},
        "language": language,
        "usage": [u.to_dict() for u in result.usage],
        "seconds": round(result.seconds, 3),
        "attempts": result.attempts,
        "truncated": result.truncated,
        "original_bytes": result.original_bytes,
        "budget_bytes": result.budget_bytes,
    }


def encode(source: ImageSource, **options: Any) -> Packet:
    """``slop.encode(path) -> Packet``. See ``encode_detailed`` for options."""
    return encode_detailed(source, **options).packet
