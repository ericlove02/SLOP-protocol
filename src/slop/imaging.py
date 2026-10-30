"""Small Pillow helpers for grids and filmstrips. No AI was harmed in this module."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

BG = (250, 248, 243)
INK = (32, 32, 32)
MUTED = (110, 110, 110)


@lru_cache(maxsize=8)
def font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def fit(image: Image.Image, box: int) -> Image.Image:
    """Scale to fit a ``box``x``box`` square, letterboxed on the background colour."""
    image = image.convert("RGB")
    scale = box / max(image.size)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    tile = Image.new("RGB", (box, box), BG)
    tile.paste(resized, ((box - resized.width) // 2, (box - resized.height) // 2))
    return tile


def labelled_row(
    images: Sequence[Image.Image],
    labels: Sequence[str],
    *,
    tile: int = 256,
    gap: int = 12,
    caption: str | None = None,
) -> Image.Image:
    """Tiles side by side, each with a label underneath, optional caption on top."""
    label_h = 44
    caption_h = 36 if caption else 0
    width = len(images) * tile + (len(images) + 1) * gap
    height = caption_h + tile + label_h + 2 * gap
    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas)
    if caption:
        draw.text((gap, gap), caption, fill=INK, font=font(20))
    for i, (img, label) in enumerate(zip(images, labels, strict=True)):
        x = gap + i * (tile + gap)
        y = gap + caption_h
        canvas.paste(fit(img, tile), (x, y))
        for j, line in enumerate(label.split("\n")[:2]):
            draw.text(
                (x, y + tile + 4 + j * 18), line, fill=INK if j == 0 else MUTED, font=font(14)
            )
    return canvas


def stack(rows: Sequence[Image.Image], gap: int = 0) -> Image.Image:
    width = max(r.width for r in rows)
    height = sum(r.height for r in rows) + gap * (len(rows) - 1)
    canvas = Image.new("RGB", (width, height), BG)
    y = 0
    for row in rows:
        canvas.paste(row, (0, y))
        y += row.height + gap
    return canvas


def filmstrip(
    images: Sequence[Image.Image],
    labels: Sequence[str] | None = None,
    *,
    tile: int = 192,
    per_row: int = 6,
    caption: str | None = None,
) -> Image.Image:
    labels = list(labels or [f"gen {i}" for i in range(len(images))])
    rows = []
    for start in range(0, len(images), per_row):
        rows.append(
            labelled_row(
                images[start : start + per_row],
                labels[start : start + per_row],
                tile=tile,
                caption=caption if start == 0 else None,
            )
        )
    return stack(rows)
