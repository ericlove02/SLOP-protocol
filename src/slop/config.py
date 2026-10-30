"""Every tunable number and model name in SLOP lives here.

Model names change faster than protocols do, so nothing else in the package
hard-codes a model ID. Each default can be overridden with an environment
variable (listed next to it) or with a CLI flag.

Prices are *estimates* used for the benchmark's cost column. They are not a
bill. Override any of them with ``SLOP_PRICES_JSON`` (see ``prices()``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_DOTENV_LOADED = False


def load_env(path: str | os.PathLike[str] | None = None) -> None:
    """Load a ``.env`` file once, without overriding variables already set."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED and path is None:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a core dependency
        return
    load_dotenv(dotenv_path=path or Path.cwd() / ".env", override=False)
    _DOTENV_LOADED = True


def env(name: str, default: str | None = None) -> str | None:
    """Read an env var, treating empty strings as unset."""
    value = os.environ.get(name)
    return value if value else default


# --------------------------------------------------------------------------
# API keys (names only; SLOP never prints the values)
# --------------------------------------------------------------------------

KEY_ANTHROPIC = "ANTHROPIC_API_KEY"
KEY_OPENAI = "OPENAI_API_KEY"
KEY_GEMINI = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
KEY_STABILITY = "STABILITY_API_KEY"
KEY_REPLICATE = "REPLICATE_API_TOKEN"
KEY_COMPAT_BASE_URL = "SLOP_OPENAI_COMPAT_BASE_URL"
KEY_COMPAT_API_KEY = "SLOP_OPENAI_COMPAT_API_KEY"


def gemini_key() -> str | None:
    for name in KEY_GEMINI:
        if value := env(name):
            return value
    return None


# --------------------------------------------------------------------------
# Default models. Checked against provider SDKs/docs in October 2026.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelDefault:
    env_var: str
    default: str

    def get(self) -> str:
        return env(self.env_var, self.default) or self.default


ANTHROPIC_VISION_MODEL = ModelDefault("SLOP_ANTHROPIC_VISION_MODEL", "claude-sonnet-5-5")
OPENAI_VISION_MODEL = ModelDefault("SLOP_OPENAI_VISION_MODEL", "gpt-5.4-mini")
GEMINI_VISION_MODEL = ModelDefault("SLOP_GEMINI_VISION_MODEL", "gemini-3.5-flash")
OPENAI_COMPAT_MODEL = ModelDefault("SLOP_OPENAI_COMPAT_MODEL", "llava")

OPENAI_IMAGE_MODEL = ModelDefault("SLOP_OPENAI_IMAGE_MODEL", "gpt-image-2")
GEMINI_IMAGE_MODEL = ModelDefault("SLOP_GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
STABILITY_IMAGE_MODEL = ModelDefault("SLOP_STABILITY_IMAGE_MODEL", "core")
REPLICATE_IMAGE_MODEL = ModelDefault("SLOP_REPLICATE_IMAGE_MODEL", "black-forest-labs/flux-schnell")
LOCAL_IMAGE_MODEL = ModelDefault("SLOP_LOCAL_IMAGE_MODEL", "stabilityai/sdxl-turbo")

# Effort for Anthropic models that support it. Describing a picture is not a
# hard reasoning problem, so we keep thinking cheap.
ANTHROPIC_EFFORT = ModelDefault("SLOP_ANTHROPIC_EFFORT", "low")
OPENAI_IMAGE_QUALITY = ModelDefault("SLOP_OPENAI_IMAGE_QUALITY", "medium")

# Provider auto-selection order.
VISION_AUTO_ORDER = ("anthropic", "openai", "gemini", "openai_compat")
IMAGEGEN_AUTO_ORDER = ("openai", "gemini", "stability", "replicate")

# --------------------------------------------------------------------------
# Image handling
# --------------------------------------------------------------------------

#: Longest side sent to a vision model. Bigger costs more tokens, not more truth.
VISION_MAX_SIDE = int(env("SLOP_VISION_MAX_SIDE", "1568") or 1568)
#: Size the receiver asks for when the packet carries no hint.
DEFAULT_OUTPUT_SIZE = (1024, 1024)

# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------

#: Named byte budgets for ``--max-bytes``. Applies to the binary packet.
BUDGETS: dict[str, int] = {
    "tweet": 280,
    "sms": 160,
    "kilobyte": 1024,
    "floppy-sector": 512,
}
#: How many times the encoder asks the model to try again before truncating.
BUDGET_RETRIES = 2
#: Output-token ceiling for descriptions with no byte budget.
MAX_DESCRIPTION_TOKENS = 4096

# --------------------------------------------------------------------------
# Prices (USD). Estimates. Edit freely, or set SLOP_PRICES_JSON.
# input/output are per million tokens; per_image is a flat fallback used when a
# provider does not report token usage for image generation.
# --------------------------------------------------------------------------

DEFAULT_PRICES: dict[str, dict[str, float]] = {
    # vision encoders
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-5.5": {"input": 5.00, "output": 30.00},
    "gemini-3.5-flash": {"input": 0.50, "output": 3.00},
    # image generators
    "gpt-image-2": {"input": 5.00, "output": 30.00, "per_image": 0.04},
    "gpt-image-1": {"input": 5.00, "output": 40.00, "per_image": 0.042},
    "gemini-3.1-flash-image": {"input": 0.50, "output": 60.00, "per_image": 0.067},
    "core": {"per_image": 0.03},
    "black-forest-labs/flux-schnell": {"per_image": 0.003},
    "stabilityai/sdxl-turbo": {"per_image": 0.0},
}


def prices() -> dict[str, dict[str, float]]:
    table = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    override = env("SLOP_PRICES_JSON")
    if override:
        for model, entry in json.loads(override).items():
            table.setdefault(model, {}).update(entry)
    return table


# --------------------------------------------------------------------------
# Joke constants, used by the "effective bandwidth" table. Sourced loosely.
# --------------------------------------------------------------------------

#: Bits per second of a 56k modem on a good day (it was never a good day).
MODEM_BPS = 53_000
#: A homing pigeon: ~80 km/h, carrying a 1 TB microSD card, over 50 km.
PIGEON_PAYLOAD_BYTES = 1_000_000_000_000
PIGEON_SECONDS = 50 / 80 * 3600
PIGEON_COST_USD = 0.05  # birdseed
#: Rough electricity guesses, clearly labelled as such wherever they are shown.
JOULES_PER_VISION_CALL = 300.0
JOULES_PER_IMAGE_GENERATION = 3_000.0
