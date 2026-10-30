"""Stability AI Stable Image as a receiver (optional extra)."""

from __future__ import annotations

import time

from PIL import Image

from slop import config
from slop.providers.base import (
    MissingKeyError,
    ProviderError,
    Usage,
    load_image_bytes,
    nearest_aspect,
    require,
)

_ENDPOINT = "https://api.stability.ai/v2beta/stable-image/generate/{model}"
_ASPECTS = ("1:1", "16:9", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21")
#: The API rejects prompts longer than this.
_MAX_PROMPT = 10_000


class StabilityImageGen:
    name = "stability"
    supports_seed = True

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.STABILITY_IMAGE_MODEL.get()
        self._key = api_key or config.env(config.KEY_STABILITY)
        if not self._key:
            raise MissingKeyError(f"set {config.KEY_STABILITY} to use the Stability backend")
        self._httpx = require("httpx", "stability")
        self.last_usage: Usage | None = None

    def generate(self, prompt: str, size: tuple[int, int], seed: int | None = None) -> Image.Image:
        data = {
            "prompt": prompt[:_MAX_PROMPT],
            "aspect_ratio": nearest_aspect(size, _ASPECTS),
            "output_format": "png",
        }
        if seed is not None:
            data["seed"] = str(seed)
        start = time.perf_counter()
        response = self._httpx.post(
            _ENDPOINT.format(model=self.model),
            headers={"authorization": f"Bearer {self._key}", "accept": "image/*"},
            files={"none": ("", b"")},
            data=data,
            timeout=120,
        )
        elapsed = time.perf_counter() - start
        if response.status_code != 200:
            raise ProviderError(f"Stability returned {response.status_code}: {response.text[:200]}")
        self.last_usage = Usage(provider=self.name, model=self.model, images=1, seconds=elapsed)
        return load_image_bytes(response.content)
