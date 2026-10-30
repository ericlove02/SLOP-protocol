import pytest
from PIL import Image

import slop
from conftest import LONG, FakeImageGen, FakeVision
from slop import providers
from slop.encoder import BudgetError, packet_limit, truncate_to_fit
from slop.packet import OVERHEAD, Packet
from slop.providers.base import MissingKeyError, nearest_aspect


def test_encode_decode_loop(fakes, sample_image):
    vision, gen = fakes
    result = slop.encode_detailed(sample_image, vision="fake")
    packet = result.packet
    assert packet.description == LONG
    assert (packet.width, packet.height) == (320, 200)
    assert result.original_bytes == sample_image.stat().st_size
    assert packet.meta["encoder"]["model"] == "fake-vision-1"
    assert vision.calls[0]["n_images"] == 1
    assert "TERSE" not in vision.calls[0]["prompt"]

    decoded = slop.decode_detailed(packet.to_bytes(), backend="fakegen", seed=42)
    assert isinstance(decoded.image, Image.Image)
    assert LONG in gen.calls[0]["prompt"]
    assert gen.calls[0]["size"] == (320, 200)
    assert decoded.seed_honoured


def test_public_api_shapes(fakes, sample_image):
    packet = slop.encode(sample_image, vision="fake")
    assert isinstance(packet, Packet)
    image = slop.decode(packet.to_text(), backend="fakegen")
    assert isinstance(image, Image.Image)


def test_terse_sets_flag_and_prompt(fakes, sample_image):
    vision, gen = fakes
    packet = slop.encode(sample_image, vision="fake", terse=True)
    assert packet.terse
    assert "TERSE MODE" in vision.calls[0]["prompt"]
    slop.decode(packet, backend="fakegen")
    assert "telegraphic" in gen.calls[0]["prompt"]


def test_budget_respected_after_retry(sample_image):
    short = "Night sky, cobalt #1F3A93 swirls, gold moon top R, cypress L."
    vision = FakeVision([LONG, short])
    result = slop.encode_detailed(sample_image, vision=vision, max_bytes=120)
    assert len(result.packet.to_bytes()) <= 120
    assert result.attempts == 2
    assert not result.truncated
    assert result.packet.description == short
    # the retry carried the previous attempt and asked for less than the target
    assert vision.calls[1]["history"][0][0] == LONG
    assert vision.calls[0]["budget"] == 120 - OVERHEAD


def test_budget_truncates_stubborn_model(sample_image):
    vision = FakeVision([LONG])  # never gets shorter
    result = slop.encode_detailed(sample_image, vision=vision, max_bytes=160, retries=2)
    assert result.attempts == 3
    assert result.truncated
    assert len(result.packet.to_bytes()) <= 160
    assert LONG.startswith(result.packet.description)


def test_max_chars_limits_text_form(sample_image):
    vision = FakeVision([LONG])
    result = slop.encode_detailed(sample_image, vision=vision, max_chars=280, retries=0)
    assert len(result.packet.to_text()) <= 280


@pytest.mark.parametrize("limit", [1, OVERHEAD])
def test_impossible_budget(limit):
    with pytest.raises(BudgetError):
        packet_limit(max_bytes=limit)


def test_truncate_single_huge_word():
    packet = truncate_to_fit("x" * 500, 40, False, (0, 0), "none")
    assert len(packet.to_bytes()) <= 40
    assert packet.description


def test_auto_without_keys_is_helpful():
    with pytest.raises(MissingKeyError, match="OPENAI_API_KEY or GEMINI_API_KEY"):
        providers.get_imagegen("auto")
    with pytest.raises(MissingKeyError, match="ANTHROPIC_API_KEY"):
        providers.get_vision("auto")


def test_auto_prefers_configured_order(monkeypatch):
    monkeypatch.setitem(providers.IMAGEGEN, "openai", lambda **kw: FakeImageGen())
    monkeypatch.setitem(providers.IMAGEGEN, "gemini", lambda **kw: "gemini!")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert providers.get_imagegen("auto") == "gemini!"
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert isinstance(providers.get_imagegen("auto"), FakeImageGen)


def test_named_provider_without_key():
    with pytest.raises(MissingKeyError, match="ANTHROPIC_API_KEY"):
        providers.get_vision("anthropic")


def test_unknown_provider():
    with pytest.raises(providers.ProviderError, match="unknown"):
        providers.get_imagegen("crayons")


def test_nearest_aspect():
    sizes = ("1024x1024", "1536x1024", "1024x1536")
    assert nearest_aspect((1920, 1080), sizes) == "1536x1024"
    assert nearest_aspect((600, 900), sizes) == "1024x1536"
    assert nearest_aspect((500, 500), ("1:1", "16:9")) == "1:1"


def test_usage_cost_estimate():
    from slop.providers.base import Usage

    u = Usage("anthropic", "claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=100_000)
    assert u.cost_usd == pytest.approx(2.0 + 1.0)
    img = Usage("stability", "core", images=2)
    assert img.cost_usd == pytest.approx(0.06)
    assert Usage("x", "unknown-model", input_tokens=10).cost_usd == 0.0
