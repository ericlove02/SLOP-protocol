"""Provider registry. ``auto`` picks the first provider with a key present."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from slop import config
from slop.providers.base import (
    ImageGenProvider,
    MissingDependencyError,
    MissingKeyError,
    ProviderError,
    Usage,
    VisionProvider,
)

__all__ = [
    "IMAGEGEN",
    "VISION",
    "ImageGenProvider",
    "MissingDependencyError",
    "MissingKeyError",
    "ProviderError",
    "Usage",
    "VisionProvider",
    "available",
    "get_imagegen",
    "get_vision",
    "register_imagegen",
    "register_vision",
]

Factory = Callable[..., Any]


def _anthropic(**kw: Any) -> VisionProvider:
    from slop.providers.anthropic import AnthropicVision

    return AnthropicVision(**kw)


def _openai_vision(**kw: Any) -> VisionProvider:
    from slop.providers.openai import OpenAIVision

    return OpenAIVision(**kw)


def _gemini_vision(**kw: Any) -> VisionProvider:
    from slop.providers.gemini import GeminiVision

    return GeminiVision(**kw)


def _compat_vision(**kw: Any) -> VisionProvider:
    from slop.providers.openai_compat import OpenAICompatVision

    return OpenAICompatVision(**kw)


def _openai_image(**kw: Any) -> ImageGenProvider:
    from slop.providers.openai import OpenAIImageGen

    return OpenAIImageGen(**kw)


def _gemini_image(**kw: Any) -> ImageGenProvider:
    from slop.providers.gemini import GeminiImageGen

    return GeminiImageGen(**kw)


def _stability_image(**kw: Any) -> ImageGenProvider:
    from slop.providers.stability import StabilityImageGen

    return StabilityImageGen(**kw)


def _replicate_image(**kw: Any) -> ImageGenProvider:
    from slop.providers.replicate import ReplicateImageGen

    return ReplicateImageGen(**kw)


def _local_image(**kw: Any) -> ImageGenProvider:
    from slop.providers.local_diffusers import LocalDiffusersImageGen

    return LocalDiffusersImageGen(**kw)


VISION: dict[str, Factory] = {
    "anthropic": _anthropic,
    "openai": _openai_vision,
    "gemini": _gemini_vision,
    "openai_compat": _compat_vision,
}

IMAGEGEN: dict[str, Factory] = {
    "openai": _openai_image,
    "gemini": _gemini_image,
    "stability": _stability_image,
    "replicate": _replicate_image,
    "local": _local_image,
}

#: Which env var(s) make each provider eligible for ``auto``.
_KEY_CHECKS: dict[str, Callable[[], bool]] = {
    "anthropic": lambda: bool(config.env(config.KEY_ANTHROPIC)),
    "openai": lambda: bool(config.env(config.KEY_OPENAI)),
    "gemini": lambda: bool(config.gemini_key()),
    "openai_compat": lambda: bool(config.env(config.KEY_COMPAT_BASE_URL)),
    "stability": lambda: bool(config.env(config.KEY_STABILITY)),
    "replicate": lambda: bool(config.env(config.KEY_REPLICATE)),
    "local": lambda: False,  # never auto-selected; it downloads gigabytes
}

_VISION_KEYS_HELP = (
    f"{config.KEY_ANTHROPIC}, {config.KEY_OPENAI}, {' or '.join(config.KEY_GEMINI)}, "
    f"or {config.KEY_COMPAT_BASE_URL}"
)
_IMAGEGEN_KEYS_HELP = (
    f"{config.KEY_OPENAI} or {' / '.join(config.KEY_GEMINI)} "
    f"(or optionally {config.KEY_STABILITY}, {config.KEY_REPLICATE}, or --backend local)"
)


def register_vision(name: str, factory: Factory, has_key: Callable[[], bool] | None = None) -> None:
    VISION[name] = factory
    if has_key is not None:
        _KEY_CHECKS[name] = has_key


def register_imagegen(
    name: str, factory: Factory, has_key: Callable[[], bool] | None = None
) -> None:
    IMAGEGEN[name] = factory
    if has_key is not None:
        _KEY_CHECKS[name] = has_key


def has_key(name: str) -> bool:
    check = _KEY_CHECKS.get(name)
    return bool(check and check())


def available() -> dict[str, list[str]]:
    """Which providers could run right now (keys present; SDKs not checked)."""
    return {
        "vision": [n for n in VISION if has_key(n)],
        "imagegen": [n for n in IMAGEGEN if has_key(n)],
    }


def _resolve(
    kind: str,
    name: str,
    registry: dict[str, Factory],
    order: tuple[str, ...],
    keys_help: str,
    **kwargs: Any,
):
    config.load_env()
    if name == "auto":
        candidates = [n for n in order if n in registry and has_key(n)]
        candidates += [n for n in registry if n not in order and has_key(n)]
        if not candidates:
            raise MissingKeyError(f"no {kind} provider is configured. Set one of: {keys_help}.")
        name = candidates[0]
    if name not in registry:
        raise ProviderError(f"unknown {kind} provider {name!r}; choose from {sorted(registry)}")
    return registry[name](**kwargs)


def get_vision(name: str = "auto", **kwargs: Any) -> VisionProvider:
    return _resolve(
        "vision (encoder)", name, VISION, config.VISION_AUTO_ORDER, _VISION_KEYS_HELP, **kwargs
    )


def get_imagegen(name: str = "auto", **kwargs: Any) -> ImageGenProvider:
    return _resolve(
        "image-generation (decoder)",
        name,
        IMAGEGEN,
        config.IMAGEGEN_AUTO_ORDER,
        _IMAGEGEN_KEYS_HELP,
        **kwargs,
    )
