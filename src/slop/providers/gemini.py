"""Google Gemini: transmitter (vision) and receiver (native image generation)."""

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
    load_image_bytes,
    nearest_aspect,
    require,
    to_png_bytes,
)

_ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")


def _client(api_key: str | None):
    key = api_key or config.gemini_key()
    if not key:
        raise MissingKeyError(f"set {' or '.join(config.KEY_GEMINI)} to use Gemini")
    genai = require("google.genai", "gemini")
    return genai.Client(api_key=key), require("google.genai.types", "gemini")


def _usage(name: str, model: str, response, seconds: float, images: int = 0) -> Usage:
    meta = getattr(response, "usage_metadata", None)
    return Usage(
        provider=name,
        model=model,
        input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
        output_tokens=int(
            (getattr(meta, "candidates_token_count", 0) or 0)
            + (getattr(meta, "thoughts_token_count", 0) or 0)
        ),
        images=images,
        seconds=seconds,
    )


def _parts(response) -> list:
    candidates = getattr(response, "candidates", None) or []
    if not candidates or not candidates[0].content:
        return []
    return list(candidates[0].content.parts or [])


class GeminiVision:
    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.GEMINI_VISION_MODEL.get()
        self._client, self._types = _client(api_key)
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
        types = self._types
        user_parts = [
            types.Part.from_bytes(data=to_png_bytes(img), mime_type="image/png")
            for img in as_list(image)
        ]
        user_parts.append(types.Part.from_text(text=instruction))
        contents = [types.Content(role="user", parts=user_parts)]
        for assistant_text, user_text in history:
            contents.append(
                types.Content(role="model", parts=[types.Part.from_text(text=assistant_text)])
            )
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
            )
        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=prompt),
        )
        elapsed = time.perf_counter() - start
        text = (getattr(response, "text", None) or "").strip()
        self.last_usage = _usage(self.name, self.model, response, elapsed)
        if not text:
            raise ProviderError("Gemini returned an empty description")
        return text


class GeminiImageGen:
    name = "gemini"
    supports_seed = True

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.GEMINI_IMAGE_MODEL.get()
        self._client, self._types = _client(api_key)
        self.last_usage: Usage | None = None

    def generate(self, prompt: str, size: tuple[int, int], seed: int | None = None) -> Image.Image:
        types = self._types
        cfg = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=nearest_aspect(size, _ASPECTS)),
            seed=seed,
        )
        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.model, contents=[prompt], config=cfg
        )
        elapsed = time.perf_counter() - start
        for part in _parts(response):
            inline = getattr(part, "inline_data", None)
            if inline is not None and inline.data:
                self.last_usage = _usage(self.name, self.model, response, elapsed, images=1)
                return load_image_bytes(inline.data)
        reason = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = f" (finish_reason={getattr(candidates[0], 'finish_reason', None)})"
        raise ProviderError(f"Gemini returned no image{reason}")
