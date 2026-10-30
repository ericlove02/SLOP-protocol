"""Anthropic Claude as a SLOP transmitter.

Claude can look at images but cannot make them, so this module only implements
VisionProvider. The receiver always needs a real image generator.
"""

from __future__ import annotations

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
    require,
)

#: Models that accept the server-side refusal fallback ("default" form).
_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Models that accept output_config.effort.
_EFFORT_PREFIXES = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-opus-4",
    "claude-fable",
    "claude-haiku-5",
)


class AnthropicVision:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.ANTHROPIC_VISION_MODEL.get()
        key = api_key or config.env(config.KEY_ANTHROPIC)
        if not key:
            raise MissingKeyError(f"set {config.KEY_ANTHROPIC} to use the Anthropic encoder")
        sdk = require("anthropic", "anthropic")
        self._sdk = sdk
        self._client = sdk.Anthropic(api_key=key)
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
        content: list[dict] = []
        for img in as_list(image):
            media_type, data = image_payload(img)
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            )
        content.append({"type": "text", "text": instruction})
        messages: list[dict] = [{"role": "user", "content": content}]
        for assistant_text, user_text in history:
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({"role": "user", "content": user_text})

        params: dict = {
            "model": self.model,
            "max_tokens": 16_000,
            "system": prompt,
            "messages": messages,
        }
        if self.model.startswith(_EFFORT_PREFIXES):
            params["output_config"] = {"effort": config.ANTHROPIC_EFFORT.get()}

        start = time.perf_counter()
        response = self._create(params)
        elapsed = time.perf_counter() - start

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ProviderError(f"Claude declined to describe this image ({details})")
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        usage = response.usage
        self.last_usage = Usage(
            provider=self.name,
            model=self.model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            seconds=elapsed,
        )
        if not text:
            raise ProviderError("Claude returned an empty description")
        return text

    def _create(self, params: dict):
        if self.model in _FALLBACK_MODELS:
            try:
                return self._client.beta.messages.create(
                    **params, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            except self._sdk.BadRequestError:
                # Older accounts/regions may not accept the fallback beta; the
                # plain request is still a perfectly good request.
                pass
        return self._client.messages.create(**params)
