"""Benchmark metrics: sizes, similarity, cost, and some numbers we made up on purpose."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from slop import config, prompts
from slop.providers import Usage, VisionProvider

# --------------------------------------------------------------------------
# sizes
# --------------------------------------------------------------------------


def file_size(path: str | Path) -> int:
    return Path(path).stat().st_size


def ratio(original_bytes: int, packet_bytes: int) -> float:
    return original_bytes / packet_bytes if packet_bytes else float("inf")


# --------------------------------------------------------------------------
# SSIM (pixel similarity). Implemented here so the core needs only numpy.
# --------------------------------------------------------------------------


def _gaussian_kernel(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    x = np.arange(size) - size // 2
    k = np.exp(-(x**2) / (2 * sigma**2))
    return k / k.sum()


def _blur(img: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    pad = len(kernel) // 2
    padded = np.pad(img, pad, mode="reflect")
    rows = np.apply_along_axis(lambda r: np.convolve(r, kernel, mode="valid"), 1, padded)
    return np.apply_along_axis(lambda c: np.convolve(c, kernel, mode="valid"), 0, rows)


def _gray(image: Image.Image, side: int) -> np.ndarray:
    return np.asarray(
        image.convert("L").resize((side, side), Image.Resampling.BILINEAR), dtype=np.float64
    )


def ssim(a: Image.Image, b: Image.Image, side: int = 256) -> float:
    """Mean SSIM (Wang et al. 2004) on grayscale images resized to ``side``x``side``.

    Both images are squashed to the same square so that a received image with a
    different aspect ratio can still be compared. Expect low numbers: SLOP does
    not transmit pixels, and SSIM only cares about pixels.
    """
    x, y = _gray(a, side), _gray(b, side)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k = _gaussian_kernel()
    mu_x, mu_y = _blur(x, k), _blur(y, k)
    sxx = _blur(x * x, k) - mu_x**2
    syy = _blur(y * y, k) - mu_y**2
    sxy = _blur(x * y, k) - mu_x * mu_y
    num = (2 * mu_x * mu_y + c1) * (2 * sxy + c2)
    den = (mu_x**2 + mu_y**2 + c1) * (sxx + syy + c2)
    return float(np.mean(num / den))


# --------------------------------------------------------------------------
# CLIP similarity (semantic), optional
# --------------------------------------------------------------------------

_CLIP = None


def clip_available() -> bool:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def clip_similarity(a: Image.Image, b: Image.Image) -> float | None:
    """Cosine similarity of CLIP image embeddings, or None without open_clip."""
    global _CLIP
    if not clip_available():
        return None
    import open_clip
    import torch

    if _CLIP is None:
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        model.eval()
        _CLIP = (model, preprocess)
    model, preprocess = _CLIP
    with torch.no_grad():
        batch = torch.stack([preprocess(a.convert("RGB")), preprocess(b.convert("RGB"))])
        feats = model.encode_image(batch)
        feats = feats / feats.norm(dim=-1, keepdim=True)
        return float((feats[0] @ feats[1]).item())


# --------------------------------------------------------------------------
# Vibe Score
# --------------------------------------------------------------------------

_SCORE_RE = re.compile(r"SCORE:\s*(\d+(?:\.\d+)?)\s*\|?\s*(.*)", re.IGNORECASE | re.DOTALL)


@dataclass
class Vibe:
    score: float | None
    reason: str
    usage: Usage | None


def parse_vibe(text: str) -> tuple[float | None, str]:
    match = _SCORE_RE.search(text)
    if not match:
        return None, text.strip()
    score = max(1.0, min(10.0, float(match.group(1))))
    return score, match.group(2).strip().splitlines()[0] if match.group(2).strip() else ""


def vibe_score(judge: VisionProvider, original: Image.Image, received: Image.Image) -> Vibe:
    """Ask a vision model: is this the same picture, spiritually?"""
    text = judge.describe(
        [original, received], prompts.VIBE_SYSTEM, instruction=prompts.VIBE_INSTRUCTION
    )
    score, reason = parse_vibe(text)
    return Vibe(score, reason, judge.last_usage)


# --------------------------------------------------------------------------
# Joke metrics, clearly labelled as such wherever they are displayed
# --------------------------------------------------------------------------


def joules_ish(vision_calls: int, generations: int) -> float:
    """A rough guess at energy use. Not a measurement. See config for the constants."""
    return (
        vision_calls * config.JOULES_PER_VISION_CALL
        + generations * config.JOULES_PER_IMAGE_GENERATION
    )


@dataclass
class Bandwidth:
    name: str
    bytes_per_second: float
    bytes_per_dollar: float | None
    note: str = ""


def effective_bandwidth(bytes_saved: float, seconds: float, dollars: float) -> Bandwidth:
    """SLOP's 'bandwidth': bytes *not* sent, per second waited and per dollar spent."""
    return Bandwidth(
        "SLOP",
        bytes_saved / seconds if seconds else float("inf"),
        bytes_saved / dollars if dollars else None,
    )


def reference_links() -> list[Bandwidth]:
    return [
        Bandwidth("56k modem", config.MODEM_BPS / 8, None, "≈ free (it's a phone line)"),
        Bandwidth(
            "Carrier pigeon + 1 TB microSD",
            config.PIGEON_PAYLOAD_BYTES / config.PIGEON_SECONDS,
            config.PIGEON_PAYLOAD_BYTES / config.PIGEON_COST_USD,
        ),
    ]
