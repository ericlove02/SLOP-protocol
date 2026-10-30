"""OpenAI: both a transmitter (vision chat model) and a receiver (image model)."""

from __future__ import annotations

import base64
import time
from collections.abc import Sequence

from PIL import Image

from slop import config
from slop.providers.base import (
    MissingKeyError,
    ProviderError,
    Usage,
    as_list,
    image_payload,
    load_image_bytes,
    nearest_aspect,
    require,
)

#: Sizes the gpt-image family accepts.
_SIZES = ("1024x1024", "1536x1024", "1024x1536")


def _client(api_key: str | None, base_url: str | None = None):
    sdk = require("openai", "openai")
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return sdk.OpenAI(**kwargs)


def chat_vision_messages(
    images: list[Image.Image],
    prompt: str,
    instruction: str,
    history: Sequence[tuple[str, str]],
) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": instruction}]
    for img in images:
        media_type, data = image_payload(img)
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
        )
    messages: list[dict] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": content},
    ]
    for assistant_text, user_text in history:
        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": user_text})
    return messages


class OpenAIVision:
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.OPENAI_VISION_MODEL.get()
        key = api_key or config.env(config.KEY_OPENAI)
        if not key:
            raise MissingKeyError(f"set {config.KEY_OPENAI} to use the OpenAI encoder")
        self._client = _client(key)
        self.last_usage: Usage | None = None

    def describe(
        self,
        image: Image.Image | Sequence[Image.Image],
        prompt: str,
        budget: int | None = None,
        *,
        instruction: str = "Transmit this image.",
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        messages = chat_vision_messages(as_list(image), prompt, instruction, history)
        start = time.perf_counter()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_completion_tokens=config.MAX_DESCRIPTION_TOKENS * 2,
        )
        elapsed = time.perf_counter() - start
        text = (response.choices[0].message.content or "").strip()
        usage = response.usage
        self.last_usage = Usage(
            provider=self.name,
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            seconds=elapsed,
        )
        if not text:
            raise ProviderError("OpenAI returned an empty description")
        return text


class OpenAIImageGen:
    name = "openai"
    supports_seed = False

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.OPENAI_IMAGE_MODEL.get()
        key = api_key or config.env(config.KEY_OPENAI)
        if not key:
            raise MissingKeyError(f"set {config.KEY_OPENAI} to use the OpenAI image backend")
        self._client = _client(key)
        self.last_usage: Usage | None = None

    def generate(self, prompt: str, size: tuple[int, int], seed: int | None = None) -> Image.Image:
        # gpt-image models have no seed parameter; every viewing is unique.
        start = time.perf_counter()
        response = self._client.images.generate(
            model=self.model,
            prompt=prompt,
            size=nearest_aspect(size, _SIZES),
            quality=config.OPENAI_IMAGE_QUALITY.get(),
            n=1,
        )
        elapsed = time.perf_counter() - start
        if not response.data or not response.data[0].b64_json:
            raise ProviderError("OpenAI returned no image")
        image = load_image_bytes(base64.b64decode(response.data[0].b64_json))
        usage = getattr(response, "usage", None)
        self.last_usage = Usage(
            provider=self.name,
            model=self.model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            images=1,
            seconds=elapsed,
        )
        return image
