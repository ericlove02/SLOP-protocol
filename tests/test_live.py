"""Live tests against real APIs. Skipped by default; run with ``pytest -m live``.

They cost a few cents. They need real keys in the environment (or .env).
"""

import os

import pytest
from PIL import Image, ImageDraw

import slop
from slop import config

pytestmark = pytest.mark.live


config.load_env()


def _square(tmp_path):
    img = Image.new("RGB", (512, 512), (240, 240, 240))
    ImageDraw.Draw(img).rectangle((150, 150, 362, 362), fill=(220, 30, 30))
    path = tmp_path / "square.png"
    img.save(path)
    return path


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_anthropic_encode(tmp_path):
    packet = slop.encode(_square(tmp_path), vision="anthropic", max_bytes=280)
    assert len(packet.to_bytes()) <= 280
    assert "red" in packet.description.lower() or "#" in packet.description


@pytest.mark.parametrize("backend", ["openai", "gemini"])
def test_live_decode(backend, tmp_path):
    if backend == "openai" and not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("needs OPENAI_API_KEY")
    if backend == "gemini" and not config.gemini_key():
        pytest.skip("needs GEMINI_API_KEY")
    packet = slop.Packet("A single red square centred on a pale grey background.", 512, 512)
    image = slop.decode(packet, backend=backend)
    assert image.width > 0
