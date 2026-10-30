"""Exercise real provider classes against stub SDK clients (no network)."""

import base64
import io
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from slop.providers.base import ProviderError

IMG = Image.new("RGB", (64, 48), (200, 30, 30))


def png_bytes(color=(10, 200, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


class Recorder:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def anthropic_response(text="SCENE: red rectangle.", stop_reason="end_turn"):
    return NS(
        stop_reason=stop_reason,
        stop_details=None,
        content=[NS(type="thinking", thinking=""), NS(type="text", text=text)],
        usage=NS(input_tokens=1234, output_tokens=56),
    )


def test_anthropic_vision(monkeypatch):
    pytest.importorskip("anthropic")
    from slop.providers.anthropic import AnthropicVision

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicVision()
    beta = Recorder(anthropic_response())
    provider._client = NS(beta=NS(messages=NS(create=beta)), messages=NS(create=Recorder()))
    text = provider.describe(IMG, "SYSTEM", instruction="go", history=[("long", "shorter")])
    assert text == "SCENE: red rectangle."
    call = beta.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["system"] == "SYSTEM"
    assert call["fallbacks"] == "default"
    assert call["output_config"] == {"effort": "low"}
    first = call["messages"][0]["content"]
    assert first[0]["type"] == "image"
    assert base64.b64decode(first[0]["source"]["data"])
    assert [m["role"] for m in call["messages"]] == ["user", "assistant", "user"]
    assert provider.last_usage.input_tokens == 1234
    assert provider.last_usage.cost_usd > 0


def test_anthropic_falls_back_without_beta(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    import httpx

    from slop.providers.anthropic import AnthropicVision

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicVision()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    bad = anthropic.BadRequestError(
        "nope", response=httpx.Response(400, request=request), body=None
    )
    plain = Recorder(anthropic_response("plain"))
    provider._client = NS(
        beta=NS(messages=NS(create=Recorder(error=bad))), messages=NS(create=plain)
    )
    assert provider.describe(IMG, "S") == "plain"
    assert "fallbacks" not in plain.calls[0]


def test_anthropic_refusal(monkeypatch):
    pytest.importorskip("anthropic")
    from slop.providers.anthropic import AnthropicVision

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicVision(model="claude-haiku-5-5")
    plain = Recorder(anthropic_response("", stop_reason="refusal"))
    provider._client = NS(messages=NS(create=plain))
    with pytest.raises(ProviderError, match="declined"):
        provider.describe(IMG, "S")
    assert "fallbacks" not in plain.calls[0]


def test_openai_image(monkeypatch):
    pytest.importorskip("openai")
    from slop.providers.openai import OpenAIImageGen

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = OpenAIImageGen()
    gen = Recorder(
        NS(
            data=[NS(b64_json=base64.b64encode(png_bytes()).decode())],
            usage=NS(input_tokens=300, output_tokens=1056),
        )
    )
    provider._client = NS(images=NS(generate=gen))
    image = provider.generate("a green square", (1920, 1080), seed=5)
    assert image.size == (32, 32)
    assert gen.calls[0]["size"] == "1536x1024"
    assert gen.calls[0]["model"] == "gpt-image-2"
    assert "seed" not in gen.calls[0]
    assert provider.last_usage.output_tokens == 1056


def test_openai_vision(monkeypatch):
    pytest.importorskip("openai")
    from slop.providers.openai import OpenAIVision

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = OpenAIVision()
    chat = Recorder(
        NS(
            choices=[NS(message=NS(content=" words "))],
            usage=NS(prompt_tokens=900, completion_tokens=80),
        )
    )
    provider._client = NS(chat=NS(completions=NS(create=chat)))
    assert provider.describe([IMG, IMG], "SYS") == "words"
    content = chat.calls[0]["messages"][1]["content"]
    assert sum(1 for part in content if part["type"] == "image_url") == 2
    assert content[1]["image_url"]["url"].startswith("data:image/")


def gemini_response(parts):
    return NS(
        candidates=[NS(content=NS(parts=parts), finish_reason="STOP")],
        usage_metadata=NS(prompt_token_count=40, candidates_token_count=1290),
        text=None,
    )


def test_gemini_image(monkeypatch):
    pytest.importorskip("google.genai")
    from slop.providers.gemini import GeminiImageGen

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    provider = GeminiImageGen()
    gen = Recorder(
        gemini_response(
            [NS(inline_data=None, text="here you go"), NS(inline_data=NS(data=png_bytes()))]
        )
    )
    provider._client = NS(models=NS(generate_content=gen))
    image = provider.generate("a green square", (1000, 1000), seed=9)
    assert image.size == (32, 32)
    cfg = gen.calls[0]["config"]
    assert cfg.seed == 9
    assert cfg.image_config.aspect_ratio == "1:1"
    assert provider.last_usage.output_tokens == 1290


def test_gemini_image_none(monkeypatch):
    pytest.importorskip("google.genai")
    from slop.providers.gemini import GeminiImageGen

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    provider = GeminiImageGen()
    provider._client = NS(models=NS(generate_content=Recorder(gemini_response([]))))
    with pytest.raises(ProviderError, match="no image"):
        provider.generate("x", (512, 512))


def test_gemini_vision(monkeypatch):
    pytest.importorskip("google.genai")
    from slop.providers.gemini import GeminiVision

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    provider = GeminiVision()
    response = gemini_response([])
    response.text = "SCENE: red."
    gen = Recorder(response)
    provider._client = NS(models=NS(generate_content=gen))
    assert provider.describe(IMG, "SYS", history=[("a", "b")]) == "SCENE: red."
    assert gen.calls[0]["config"].system_instruction == "SYS"
    assert [c.role for c in gen.calls[0]["contents"]] == ["user", "model", "user"]


def test_compat_requires_base_url():
    from slop.providers.base import MissingKeyError
    from slop.providers.openai_compat import OpenAICompatVision

    with pytest.raises(MissingKeyError, match="SLOP_OPENAI_COMPAT_BASE_URL"):
        OpenAICompatVision()


def test_missing_sdk_message(monkeypatch):
    from slop.providers.base import MissingDependencyError, require

    with pytest.raises(MissingDependencyError, match=r"slop-protocol\[nope\]"):
        require("definitely_not_a_module_xyz", "nope")
