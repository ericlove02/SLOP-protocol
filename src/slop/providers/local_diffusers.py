"""A local Hugging Face ``diffusers`` pipeline as a receiver (optional extra).

No API key, no bill, just your own GPU (or a long afternoon on CPU). The
default model, SDXL-Turbo, only reads the first ~77 CLIP tokens of the
description, which is its own kind of lossy compression.
"""

from __future__ import annotations

import time

from PIL import Image

from slop import config
from slop.providers.base import Usage, require


class LocalDiffusersImageGen:
    name = "local"
    supports_seed = True

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or config.LOCAL_IMAGE_MODEL.get()
        self._torch = require("torch", "local")
        diffusers = require("diffusers", "local")
        device = "cuda" if self._torch.cuda.is_available() else "cpu"
        if device == "cpu" and getattr(self._torch.backends, "mps", None):
            if self._torch.backends.mps.is_available():
                device = "mps"
        dtype = self._torch.float16 if device == "cuda" else self._torch.float32
        self._device = device
        self._pipe = diffusers.AutoPipelineForText2Image.from_pretrained(
            self.model, torch_dtype=dtype
        ).to(device)
        self.last_usage: Usage | None = None

    def generate(self, prompt: str, size: tuple[int, int], seed: int | None = None) -> Image.Image:
        width, height = (max(256, min(1024, v - v % 8)) for v in size)
        generator = None
        if seed is not None:
            generator = self._torch.Generator(device=self._device).manual_seed(seed)
        turbo = "turbo" in self.model.lower()
        start = time.perf_counter()
        result = self._pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=1 if turbo else 30,
            guidance_scale=0.0 if turbo else 7.0,
            generator=generator,
        )
        elapsed = time.perf_counter() - start
        self.last_usage = Usage(
            provider=self.name,
            model=self.model,
            images=1,
            seconds=elapsed,
            extra={"device": self._device},
        )
        return result.images[0].convert("RGB")
