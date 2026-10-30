"""Fake providers so the suite runs with no keys and no money."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import pytest
from PIL import Image

from slop import providers
from slop.providers.base import Usage, as_list

LONG = (
    "SCENE: oil painting, night village under swirling sky, cobalt #1F3A93 and "
    "gold #F2C94C palette, landscape orientation. GRID: top third swirling wind "
    "spirals and eleven stars; middle third rolling hills; bottom third village "
    "with church spire. OBJECTS: black-green #1B2A1F cypress, left third, flame "
    "shaped, fills full height; crescent moon top right, glowing orange halo. "
    "LIGHT: nocturnal, stars and moon as sources. CAMERA: elevated, wide. MOOD: "
    "restless, luminous, dreaming."
)


class FakeVision:
    """Returns progressively shorter descriptions on each retry, like a model that listens."""

    name = "fake"
    model = "fake-vision-1"

    def __init__(self, texts: Sequence[str] | None = None, **_: object) -> None:
        self.texts = list(texts or [LONG])
        self.calls: list[dict] = []
        self.last_usage: Usage | None = None

    def describe(self, image, prompt, budget=None, *, instruction="", history=()):
        imgs = as_list(image)
        self.calls.append(
            {
                "n_images": len(imgs),
                "prompt": prompt,
                "budget": budget,
                "instruction": instruction,
                "history": list(history),
            }
        )
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        self.last_usage = Usage(
            self.name, self.model, input_tokens=1500, output_tokens=len(text) // 4
        )
        return text


class FakeImageGen:
    """Paints a flat colour derived from the prompt. Fidelity: vibes."""

    name = "fakegen"
    model = "fake-image-1"
    supports_seed = True

    def __init__(self, **_: object) -> None:
        self.calls: list[dict] = []
        self.last_usage: Usage | None = None

    def generate(self, prompt, size, seed=None):
        self.calls.append({"prompt": prompt, "size": size, "seed": seed})
        digest = hashlib.sha256(f"{prompt}{seed}".encode()).digest()
        self.last_usage = Usage(self.name, self.model, images=1)
        return Image.new("RGB", (min(size[0], 64), min(size[1], 64)), tuple(digest[:3]))


class FakeJudge(FakeVision):
    def __init__(self, **_: object) -> None:
        super().__init__(["SCORE: 7 | Same night, fewer swirls."])


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    """Make sure no test can accidentally reach a paid API."""
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "STABILITY_API_KEY",
        "REPLICATE_API_TOKEN",
        "SLOP_OPENAI_COMPAT_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("slop.config._DOTENV_LOADED", True)


@pytest.fixture
def fakes(monkeypatch):
    """Register fake providers under the names 'fake' and 'fakegen'."""
    vision = FakeVision()
    gen = FakeImageGen()
    monkeypatch.setitem(providers.VISION, "fake", lambda **kw: vision)
    monkeypatch.setitem(providers.IMAGEGEN, "fakegen", lambda **kw: gen)
    monkeypatch.setitem(providers._KEY_CHECKS, "fake", lambda: True)
    monkeypatch.setitem(providers._KEY_CHECKS, "fakegen", lambda: True)
    return vision, gen


@pytest.fixture
def sample_image(tmp_path):
    img = Image.new("RGB", (320, 200), (30, 60, 140))
    for x in range(100, 160):
        for y in range(50, 120):
            img.putpixel((x, y), (240, 200, 70))
    path = tmp_path / "sample.png"
    img.save(path)
    return path
