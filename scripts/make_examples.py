"""Regenerate the procedural example images in examples/originals/.

Deterministic (fixed seeds), so the committed files can be rebuilt byte-for-byte
on the same Pillow/matplotlib versions. The two non-procedural images (the
public-domain painting and the CC0 coffee photo) are documented in
examples/SOURCES.md and copied by --fetch-external.

    python scripts/make_examples.py               # procedural images only
    python scripts/make_examples.py --fetch-external
"""

from __future__ import annotations

import argparse
import math
import random
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "examples" / "originals"

STARRY_NIGHT_URL = "https://raw.githubusercontent.com/jcjohnson/neural-style/master/examples/inputs/starry_night.jpg"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["DejaVuSans-Bold.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "Arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def geometric() -> Image.Image:
    """The easy one: flat colours, hard edges, three shapes."""
    img = Image.new("RGB", (768, 768), "#1E3A8A")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 576, 768, 768), fill="#F5E6C8")
    d.ellipse((96, 120, 352, 376), fill="#E63946")
    d.polygon([(560, 96), (700, 360), (420, 360)], fill="#FFB703")
    d.rectangle((300, 420, 500, 620), fill="#2A9D8F")
    return img


def _ridge(width: int, rng: random.Random, base: float, rough: float) -> list[float]:
    """1-D midpoint displacement."""
    n = 1
    while n < width:
        n *= 2
    pts = [0.0] * (n + 1)
    pts[0], pts[n] = base + rng.uniform(-rough, rough), base + rng.uniform(-rough, rough)
    step, scale = n, rough
    while step > 1:
        half = step // 2
        for i in range(half, n, step):
            pts[i] = (pts[i - half] + pts[i + half]) / 2 + rng.uniform(-scale, scale)
        step, scale = half, scale * 0.55
    return pts[:width]


def landscape() -> Image.Image:
    """Sunset, three mountain ranges, a lake that reflects some of it."""
    w, h = 1024, 640
    rng = random.Random(7)
    sky = np.zeros((h, w, 3), dtype=np.float32)
    top, bottom = np.array([38, 28, 92]), np.array([253, 160, 90])
    for y in range(h):
        t = min(1.0, y / (h * 0.62))
        sky[y, :] = top * (1 - t) + bottom * t
    img = Image.fromarray(sky.astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.ellipse((640, 250, 760, 370), fill="#FFE29A")
    horizon = int(h * 0.68)
    for base, rough, colour in ((300, 90, "#5B4B8A"), (360, 70, "#3D3266"), (410, 50, "#241E3F")):
        ridge = _ridge(w, rng, base, rough)
        d.polygon([(0, horizon)] + [(x, ridge[x]) for x in range(w)] + [(w, horizon)], fill=colour)
    # pines on the near shore
    for _ in range(40):
        x = rng.randint(0, w)
        hgt = rng.randint(30, 70)
        d.polygon(
            [(x, horizon - hgt), (x - hgt // 4, horizon), (x + hgt // 4, horizon)], fill="#141226"
        )
    # lake: mirrored, darkened, rippled
    top_half = img.crop((0, horizon - (h - horizon), w, horizon)).transpose(
        Image.Transpose.FLIP_TOP_BOTTOM
    )
    arr = np.asarray(top_half).astype(np.float32) * 0.6 + np.array([10, 20, 40]) * 0.4
    lake = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    img.paste(lake, (0, horizon))
    d = ImageDraw.Draw(img)
    for y in range(horizon + 6, h, 9):
        x0 = rng.randint(0, w - 200)
        d.line((x0, y, x0 + rng.randint(60, 220), y), fill="#F7C68A", width=1)
    return img


def market() -> Image.Image:
    """A busy night market: stalls, striped awnings, lanterns, a crowd."""
    w, h = 1024, 683
    rng = random.Random(11)
    img = Image.new("RGB", (w, h), "#141A33")
    d = ImageDraw.Draw(img)
    # buildings with lit windows
    x = 0
    while x < w:
        bw = rng.randint(90, 170)
        bh = rng.randint(220, 380)
        d.rectangle(
            (x, 0 + 380 - bh, x + bw, 420), fill=rng.choice(["#262B4A", "#2E2440", "#1F2D3A"])
        )
        for wy in range(380 - bh + 20, 400, 34):
            for wx in range(x + 12, x + bw - 20, 30):
                if rng.random() < 0.55:
                    d.rectangle(
                        (wx, wy, wx + 14, wy + 20),
                        fill=rng.choice(["#FFD27F", "#FFE9B0", "#8FB8FF"]),
                    )
        x += bw
    # street
    d.rectangle((0, 420, w, h), fill="#3A3536")
    # stalls with striped awnings
    stall_colours = [
        ("#D62828", "#FFFFFF"),
        ("#2A9D8F", "#F1FAEE"),
        ("#F77F00", "#FCBF49"),
        ("#6A4C93", "#FFFFFF"),
    ]
    for i, sx in enumerate(range(20, w, 205)):
        a, b = stall_colours[i % len(stall_colours)]
        d.rectangle((sx, 330, sx + 180, 470), fill="#5C3D2E")
        for k in range(6):
            d.polygon(
                [
                    (sx + k * 30, 300),
                    (sx + k * 30 + 30, 300),
                    (sx + k * 30 + 34, 340),
                    (sx + k * 30 - 4, 340),
                ],
                fill=a if k % 2 == 0 else b,
            )
        for _ in range(9):  # produce on the counter
            cx, cy = sx + rng.randint(10, 170), rng.randint(350, 380)
            r = rng.randint(6, 11)
            d.ellipse(
                (cx - r, cy - r, cx + r, cy + r),
                fill=rng.choice(["#E63946", "#FFB703", "#8AC926", "#FB8500"]),
            )
    # lantern strings
    for row_y in (120, 190):
        pts = [(x, row_y + 25 * math.sin(x / 160)) for x in range(0, w + 1, 8)]
        d.line(pts, fill="#222222", width=2)
        for x in range(30, w, 64):
            y = row_y + 25 * math.sin(x / 160)
            d.ellipse(
                (x - 11, y + 2, x + 11, y + 26), fill=rng.choice(["#FF4D4D", "#FFB347", "#FFDD57"])
            )
    # crowd
    for _ in range(46):
        px = rng.randint(-10, w)
        py = rng.randint(470, 640)
        scale = 0.6 + (py - 470) / 300
        body = rng.choice(
            ["#264653", "#E76F51", "#F4A261", "#2B2D42", "#8D99AE", "#3A86FF", "#FFBE0B"]
        )
        bw2, bh2 = int(26 * scale), int(70 * scale)
        d.rounded_rectangle((px - bw2 // 2, py - bh2, px + bw2 // 2, py), radius=8, fill=body)
        hr = int(11 * scale)
        d.ellipse(
            (px - hr, py - bh2 - 2 * hr, px + hr, py - bh2),
            fill=rng.choice(["#2B1D14", "#6B4226", "#C68642", "#E0AC69"]),
        )
    return img.filter(ImageFilter.SMOOTH)


def sign() -> Image.Image:
    """Readable text is where description-based codecs go to die."""
    w, h = 900, 600
    rng = random.Random(3)
    img = Image.new("RGB", (w, h), "#8C3B2B")
    d = ImageDraw.Draw(img)
    for row, y in enumerate(range(0, h, 30)):  # brick wall
        offset = 0 if row % 2 == 0 else 45
        for x in range(-offset, w, 90):
            shade = rng.choice(["#9E4433", "#8C3B2B", "#7A3326", "#A84C3A"])
            d.rectangle((x + 2, y + 2, x + 88, y + 28), fill=shade)
    d.rounded_rectangle(
        (120, 110, 780, 470), radius=24, fill="#F2E8CF", outline="#1B4332", width=10
    )
    d.text((450, 185), "SLOP'S DINER", font=_font(72, bold=True), fill="#C1121F", anchor="mm")
    d.text((450, 270), "OPEN 24 HOURS", font=_font(44, bold=True), fill="#1B4332", anchor="mm")
    d.line((200, 315, 700, 315), fill="#1B4332", width=4)
    d.text((450, 365), "TODAY: SOUP OF THE DAY", font=_font(36), fill="#333333", anchor="mm")
    d.text((450, 420), "$3.50", font=_font(40, bold=True), fill="#C1121F", anchor="mm")
    return img


def chart() -> Image.Image:
    """A bar chart. Numbers are content too, and SLOP is not great with content."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    methods = ["Email", "Floppy", "56k modem", "Pigeon", "SLOP"]
    values = [38, 21, 9, 55, 72]
    colours = ["#457B9D", "#A8DADC", "#1D3557", "#E9C46A", "#E63946"]
    fig, ax = plt.subplots(figsize=(8, 5), dpi=100)
    bars = ax.bar(methods, values, color=colours)
    ax.set_title("Image delivery satisfaction by method (n=200)", fontsize=14)
    ax.set_ylabel("Satisfied respondents (%)")
    ax.set_ylim(0, 100)
    ax.bar_label(bars, labels=[f"{v}%" for v in values], padding=3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.canvas.draw()
    img = Image.frombuffer("RGBA", fig.canvas.get_width_height(), fig.canvas.buffer_rgba()).convert(
        "RGB"
    )
    plt.close(fig)
    return img


def noise() -> Image.Image:
    """Uniform RGB noise: maximum entropy, minimum describability. The nemesis."""
    rng = np.random.default_rng(1234)
    return Image.fromarray(rng.integers(0, 256, size=(512, 512, 3), dtype=np.uint8))


PROCEDURAL = {
    "01_geometric.png": geometric,
    "02_landscape.png": landscape,
    "05_market.png": market,
    "06_sign.png": sign,
    "07_chart.png": chart,
    "08_noise.png": noise,
}


def fetch_external() -> None:
    from skimage import data  # CC0 coffee photo ships inside scikit-image

    Image.fromarray(data.coffee()).save(OUT / "03_still_life.jpg", quality=92)
    with urllib.request.urlopen(STARRY_NIGHT_URL, timeout=60) as response:
        (OUT / "04_painting.jpg").write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-external", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in PROCEDURAL.items():
        fn().save(OUT / name, optimize=True)
        print("wrote", OUT / name)
    if args.fetch_external:
        fetch_external()
        print("fetched 03_still_life.jpg and 04_painting.jpg")


if __name__ == "__main__":
    main()
