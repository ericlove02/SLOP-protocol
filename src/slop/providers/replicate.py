"""Replicate-hosted text-to-image models as a receiver (optional extra)."""

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

_ASPECTS = ("1:1", "16:9", "21:9", "3:2", "2:3", "4:5", "5:4", "3:4", "4:3", "9:16", "9:21")


class ReplicateImageGen:
    name = "replicate"
    supports_seed = True

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.REPLICATE_IMAGE_MODEL.get()
        token = api_key or config.env(config.KEY_REPLICATE)
        if not token:
            raise MissingKeyError(f"set {config.KEY_REPLICATE} to use the Replicate backend")
        sdk = require("replicate", "replicate")
        self._client = sdk.Client(api_token=token)
        self.last_usage: Usage | None = None

    def generate(self, prompt: str, size: tuple[int, int], seed: int | None = None) -> Image.Image:
        inputs: dict = {
            "prompt": prompt,
            "aspect_ratio": nearest_aspect(size, _ASPECTS),
            "output_format": "png",
        }
        if seed is not None:
            inputs["seed"] = seed
        start = time.perf_counter()
        output = self._client.run(self.model, input=inputs)
        elapsed = time.perf_counter() - start
        first = output[0] if isinstance(output, (list, tuple)) else output
        if first is None:
            raise ProviderError("Replicate returned no image")
        data = first.read() if hasattr(first, "read") else None
        if data is None:
            raise ProviderError(f"unexpected Replicate output: {type(first).__name__}")
        self.last_usage = Usage(provider=self.name, model=self.model, images=1, seconds=elapsed)
        return load_image_bytes(data)
