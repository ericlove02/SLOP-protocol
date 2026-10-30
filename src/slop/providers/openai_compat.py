"""Any OpenAI-compatible chat endpoint as a transmitter: Ollama, LM Studio, OpenRouter...

Set ``SLOP_OPENAI_COMPAT_BASE_URL`` (e.g. ``http://localhost:11434/v1`` for
Ollama) and ``SLOP_OPENAI_COMPAT_MODEL`` (a vision-capable model such as
``llava``). Local servers usually ignore the API key; we send a placeholder.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from PIL import Image

from slop import config
from slop.providers.base import MissingKeyError, ProviderError, Usage, as_list
from slop.providers.openai import _client, chat_vision_messages


class OpenAICompatVision:
    name = "openai_compat"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.base_url = base_url or config.env(config.KEY_COMPAT_BASE_URL)
        if not self.base_url:
            raise MissingKeyError(
                f"set {config.KEY_COMPAT_BASE_URL} (e.g. http://localhost:11434/v1) "
                "to use an OpenAI-compatible encoder"
            )
        self.model = model or config.OPENAI_COMPAT_MODEL.get()
        key = api_key or config.env(config.KEY_COMPAT_API_KEY) or "not-needed"
        self._client = _client(key, self.base_url)
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
            model=self.model, messages=messages, max_tokens=config.MAX_DESCRIPTION_TOKENS
        )
        elapsed = time.perf_counter() - start
        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        self.last_usage = Usage(
            provider=self.name,
            model=self.model,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            seconds=elapsed,
        )
        if not text:
            raise ProviderError(f"{self.base_url} returned an empty description")
        return text
