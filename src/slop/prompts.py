"""Every prompt SLOP sends to a model, in one place.

A "language" is the dialect the encoder writes in. Both are English; one has
had its articles confiscated.
"""

from __future__ import annotations

LANGUAGES = ("english", "terse")

_CORE = """\
You are the transmitter of SLOP, an image-transfer protocol that sends a written \
description instead of pixels. A receiver who has never seen the image will feed \
your text to an image-generation model and must reconstruct the picture from it. \
Every byte you write is transmitted; every detail you omit is lost forever.

Write a dense, structured description in this order:
1. SCENE: one line - subject, medium/art style, overall palette, aspect/orientation.
2. GRID: what occupies each third (top/middle/bottom x left/center/right).
3. OBJECTS: each notable object with colour (common name + approximate hex), size \
relative to frame, exact position, texture/material, and relation to neighbours.
4. TEXT: any legible text, verbatim, with font style, colour and position.
5. LIGHT: light source direction, time of day, shadows, contrast.
6. CAMERA: angle, distance, lens feel, depth of field.
7. MOOD: two to four words.

Rules: maximum detail per byte. No preamble, no apology, no hedging, no filler \
("the image shows", "appears to be", "there is"). Never mention that you are \
describing an image. Do not use markdown formatting beyond the labels above. \
If the image is abstract or noise, describe its statistics precisely \
(distribution, dominant colours, grain size).\
"""

_ENGLISH = (
    _CORE
    + """

Write in plain English. Sentence fragments are fine; stay readable."""
)

_TERSE = (
    _CORE
    + """

TERSE MODE: telegraphic English. Drop articles, copulas and conjunctions. Use \
abbreviations (L/R/C, top/mid/bot, bg/fg, obj, lt, dk, ~), symbols (+, /, @, x) \
and hex codes instead of colour adjectives where shorter. Commas and semicolons, \
not sentences. The receiver is a machine; it does not need manners."""
)

SYSTEM_PROMPTS: dict[str, str] = {"english": _ENGLISH, "terse": _TERSE}


def system_prompt(language: str = "english") -> str:
    try:
        return SYSTEM_PROMPTS[language]
    except KeyError:
        raise ValueError(f"unknown language {language!r}; choose from {LANGUAGES}") from None


def describe_instruction(budget_chars: int | None = None) -> str:
    """The user-turn text that accompanies the image."""
    if budget_chars is None:
        return "Transmit this image."
    return (
        f"Transmit this image in AT MOST {budget_chars} characters total (hard limit; "
        "count carefully). Prioritise: subject and composition first, then dominant "
        "colours with hex, then positions, then everything else. Skip the section "
        "labels if they do not fit."
    )


def retry_instruction(previous: str, actual: int, limit: int) -> str:
    return (
        f"Your previous transmission was {actual} characters; the hard limit is {limit}. "
        f"Rewrite it to at most {limit} characters, keeping the most important "
        f"information. Output only the new transmission.\n\nPrevious:\n{previous}"
    )


# --------------------------------------------------------------------------
# Decoder side
# --------------------------------------------------------------------------

_GEN_TEMPLATE = """\
Create a single image that matches this description as exactly as possible. \
Follow every stated colour, position, size, and text string. Do not add a border, \
caption, watermark or extra text that is not described.{terse_note}

{description}"""

_TERSE_NOTE = (
    " The description is written in compressed telegraphic notation "
    "(L/R/C = left/right/center, bg/fg = background/foreground, hex codes are colours)."
)


def generation_prompt(description: str, backend: str, terse: bool = False) -> str:
    """Wrap a received description for the chosen image backend."""
    note = _TERSE_NOTE if terse else ""
    if backend in ("local", "local_diffusers"):
        # CLIP-based pipelines read ~77 tokens and ignore instructions anyway.
        return description
    return _GEN_TEMPLATE.format(description=description.strip(), terse_note=note)


# --------------------------------------------------------------------------
# Benchmark judge
# --------------------------------------------------------------------------

VIBE_SYSTEM = """\
You judge an image-transfer protocol that sends descriptions instead of pixels. \
You will see two images: first the ORIGINAL, then the RECEIVED reconstruction. \
Pixel-level fidelity is not expected. Rate whether they are the same picture, \
spiritually: same subject, composition, palette, mood, and any text.

Reply with exactly one line in the form:
SCORE: <integer 1-10> | <one short, dry sentence of justification>

10 = a stranger would say it is the same picture. 5 = same idea, different picture. \
1 = unrelated."""

VIBE_INSTRUCTION = "Image 1 is the ORIGINAL. Image 2 is the RECEIVED reconstruction. Score it."
