"""The two interfaces every SLOP provider implements, and shared helpers."""

from __future__ import annotations

import base64
import io
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from PIL import Image

from slop import config


class ProviderError(RuntimeError):
    """A provider could not do its one job."""


class MissingKeyError(ProviderError):
    """No usable API key for the requested provider."""


class MissingDependencyError(ProviderError):
    """An optional SDK is not installed."""


def require(module: str, extra: str) -> Any:
    """Import an optional SDK lazily, with an error that says what to install."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(
            f"this provider needs the '{module}' package: pip install 'slop-protocol[{extra}]'"
        ) from exc


@dataclass
class Usage:
    """What one provider call consumed. Token counts are as reported by the API."""

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    seconds: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["cost_usd"] = round(self.cost_usd, 6)
        return out


def estimate_cost(usage: Usage) -> float:
    price = config.prices().get(usage.model)
    if not price:
        return 0.0
    tokens_cost = (
        usage.input_tokens * price.get("input", 0.0)
        + usage.output_tokens * price.get("output", 0.0)
    ) / 1_000_000
    if usage.images and not usage.output_tokens and "per_image" in price:
        return tokens_cost + usage.images * price["per_image"]
    return tokens_cost


@runtime_checkable
class VisionProvider(Protocol):
    """Turns an image into words."""

    name: str
    model: str
    last_usage: Usage | None

    def describe(
        self,
        image: Image.Image | Sequence[Image.Image],
        prompt: str,
        budget: int | None = None,
        *,
        instruction: str = "Transmit this image.",
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        """Describe ``image`` using system ``prompt``.

        ``budget`` is a soft character limit hint (enforcement happens in the
        encoder). ``history`` is a list of (assistant, user) follow-up turns,
        used when the encoder asks for a shorter retry.
        """
        ...


@runtime_checkable
class ImageGenProvider(Protocol):
    """Turns words back into an image. Not the image. An image."""

    name: str
    model: str
    supports_seed: bool
    last_usage: Usage | None

    def generate(
        self, prompt: str, size: tuple[int, int], seed: int | None = None
    ) -> Image.Image: ...


# --------------------------------------------------------------------------
# helpers shared by concrete providers
# --------------------------------------------------------------------------


def as_list(image: Image.Image | Sequence[Image.Image]) -> list[Image.Image]:
    if isinstance(image, Image.Image):
        return [image]
    return list(image)


def prepare(image: Image.Image, max_side: int | None = None) -> Image.Image:
    """RGB, and no bigger than the vision API wants."""
    max_side = max_side or config.VISION_MAX_SIDE
    if image.mode != "RGB":
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    if max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


def to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    prepare(image).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def to_jpeg_bytes(image: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    prepare(image).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def image_payload(image: Image.Image) -> tuple[str, str]:
    """(media_type, base64) choosing whichever of PNG/JPEG is smaller."""
    png = to_png_bytes(image)
    jpg = to_jpeg_bytes(image)
    if len(jpg) < len(png):
        return "image/jpeg", base64.standard_b64encode(jpg).decode("ascii")
    return "image/png", base64.standard_b64encode(png).decode("ascii")


def nearest_aspect(size: tuple[int, int], choices: Sequence[str]) -> str:
    """Pick the 'W:H' (or 'WxH') choice closest in aspect ratio to ``size``."""
    w, h = size
    target = w / h if h else 1.0

    def ratio(choice: str) -> float:
        a, b = choice.replace("x", ":").split(":")
        return float(a) / float(b)

    import math

    return min(choices, key=lambda c: abs(math.log(ratio(c) / target)))


def load_image_bytes(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGB")
