"""Turn benchmark JSON into markdown tables, comparison grids, and README sections.

Every number in the README is rendered here from ``benchmarks/results/results.json``.
Nothing is typed in by hand except the captions in ``benchmarks/captions.json``.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from slop.bench import metrics
from slop.imaging import BG, INK, MUTED, filmstrip, fit, font, labelled_row

CAPTIONS_FILE = Path("benchmarks/captions.json")
README = Path("README.md")
SECTIONS = ("hero", "results", "languages", "backends", "sweep", "telephone", "cost")

PENDING = (
    "_No benchmark results committed yet. Run `slop bench` with your keys set "
    "(see [Benchmarks](#running-the-benchmarks)); this section is generated from "
    "`benchmarks/results/results.json`._"
)


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------


def human(n: float | int | None) -> str:
    if n is None:
        return "–"
    n = float(n)
    if n < 1024:
        return f"{n:,.0f} B"
    if n < 1024**2:
        return f"{n / 1024:,.1f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:,.1f} MB"
    if n < 1024**4:
        return f"{n / 1024**3:,.1f} GB"
    return f"{n / 1024**4:,.1f} TB"


def num(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "–"
    return f"{value:,.{digits}f}{suffix}"


def money(value: float | None) -> str:
    if value is None:
        return "–"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.3f}"


def vibe(value: float | None) -> str:
    return "–" if value is None else f"{value:g}/10"


def mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def table(headers: list[str], rows: list[list[str]], align: str | None = None) -> str:
    align = align or ("l" + "r" * (len(headers) - 1))
    marks = {"l": "---", "r": "---:", "c": ":---:"}
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(marks[a] for a in align) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# data access
# --------------------------------------------------------------------------


class Results:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.meta = data.get("meta", {})
        self.runs: dict[str, dict] = data.get("runs", {})
        self.telephone_games: dict[str, dict] = data.get("telephone", {})

    @classmethod
    def load(cls, path: Path) -> Results:
        return cls(json.loads(path.read_text()) if path.exists() else {})

    @property
    def empty(self) -> bool:
        return not any(r.get("decodes") for r in self.runs.values())

    def matrix(self) -> list[dict]:
        return [
            r
            for r in self.runs.values()
            if "matrix" in r.get("kinds", []) and "error" not in r.get("encode", {})
        ]

    def sweep(self) -> list[dict]:
        rows = [r for r in self.runs.values() if "sweep" in r.get("kinds", [])]
        return sorted(rows, key=lambda r: -(r["budget"] or 10**9))

    def backends(self) -> list[str]:
        seen: list[str] = []
        for r in self.matrix():
            for b in r.get("decodes", {}):
                if b not in seen:
                    seen.append(b)
        return seen

    def images(self) -> list[str]:
        out: list[str] = []
        for r in self.matrix():
            if r["image"] not in out:
                out.append(r["image"])
        return sorted(out)

    def get(self, image: str, language: str) -> dict | None:
        for r in self.matrix():
            if r["image"] == image and r["language"] == language:
                return r
        return None

    def ok_decodes(self) -> list[tuple[dict, str, dict]]:
        out = []
        for r in self.matrix():
            for backend, d in r.get("decodes", {}).items():
                if "error" not in d:
                    out.append((r, backend, d))
        return out


def captions() -> dict[str, str]:
    if CAPTIONS_FILE.exists():
        return json.loads(CAPTIONS_FILE.read_text())
    return {}


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------


def _open(path: str) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGB")
    except (OSError, FileNotFoundError):
        return None


def comparison_grid(res: Results, image: str, originals: Path, out: Path) -> Path | None:
    original = _open(str(originals / image))
    if original is None:
        return None
    tiles, labels = [original], [f"original\n{human((originals / image).stat().st_size)}"]
    for language in ("english", "terse"):
        run = res.get(image, language)
        if not run:
            continue
        for backend, d in run.get("decodes", {}).items():
            img = _open(d.get("image_file", "")) if "error" not in d else None
            if img is None:
                continue
            tiles.append(img)
            labels.append(
                f"{backend} · {language}\n{run['packet_bytes']} B · vibe {vibe(d.get('vibe_score'))}"
            )
    if len(tiles) == 1:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    labelled_row(tiles, labels, tile=240).save(out, optimize=True)
    return out


def hero(res: Results, originals: Path, out: Path) -> tuple[Path, dict, str, dict] | None:
    """Pick the best-scoring decode of the painting (or anything) for the top of the README."""
    candidates = [(r, b, d) for r, b, d in res.ok_decodes() if r["language"] == "english"]
    if not candidates:
        return None
    preferred = [c for c in candidates if "painting" in c[0]["image"]] or candidates
    run, backend, d = max(
        preferred, key=lambda c: (c[2].get("vibe_score") or 0, -c[0]["packet_bytes"])
    )
    original = _open(str(originals / run["image"]))
    received = _open(d["image_file"])
    if original is None or received is None:
        return None
    tile, gap, top = 460, 30, 90
    canvas = Image.new("RGB", (tile * 2 + gap * 3, tile + top + 70), BG)
    draw = ImageDraw.Draw(canvas)
    sizes = f"{human(run['original_bytes'])}  ->  {run['packet_bytes']} bytes"
    draw.text((canvas.width // 2, 45), sizes, fill=INK, font=font(44), anchor="mm")
    canvas.paste(fit(original, tile), (gap, top))
    canvas.paste(fit(received, tile), (gap * 2 + tile, top))
    draw.text((gap, top + tile + 12), "sent", fill=INK, font=font(22))
    draw.text((gap * 2 + tile, top + tile + 12), f"received ({backend})", fill=INK, font=font(22))
    draw.text(
        (gap * 2 + tile, top + tile + 40),
        f"vibe score {vibe(d.get('vibe_score'))}",
        fill=MUTED,
        font=font(18),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=True)
    return out, run, backend, d


def sweep_strip(res: Results, originals: Path, out: Path) -> Path | None:
    rows = res.sweep()
    if not rows:
        return None
    original = _open(str(originals / rows[0]["image"]))
    tiles, labels = [original] if original else [], ["original"] if original else []
    for r in rows:
        for _backend, d in r.get("decodes", {}).items():
            img = _open(d.get("image_file", ""))
            if img is None:
                continue
            budget = "full detail" if r["budget"] is None else f"budget {r['budget']} B"
            tiles.append(img)
            labels.append(f"{budget}\n{r['packet_bytes']} B · vibe {vibe(d.get('vibe_score'))}")
            break
    if len(tiles) < 2:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    labelled_row(tiles, labels, tile=240).save(out, optimize=True)
    return out


def telephone_strip(game: dict, originals: Path, out: Path) -> Path | None:
    original = _open(str(originals / game["image"]))
    images = [original] if original else []
    labels = ["original"] if original else []
    for rnd in game.get("rounds", []):
        img = _open(rnd["image_file"])
        if img is None:
            continue
        images.append(img)
        labels.append(f"gen {rnd['index']}\nvibe {vibe(rnd.get('vibe_score'))}")
    if len(images) < 2:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    filmstrip(images, labels, tile=170, per_row=6).save(out, optimize=True)
    return out


# --------------------------------------------------------------------------
# markdown sections
# --------------------------------------------------------------------------


#: Links in generated markdown are relative to this directory (the README's).
_BASE = Path(".")


def _link(path: Path) -> str:
    try:
        return Path(os.path.relpath(path, _BASE)).as_posix()
    except ValueError:  # pragma: no cover - different drive on Windows
        return path.as_posix()


def _img(path: Path | None, alt: str) -> str:
    return f"![{alt}]({_link(path)})" if path else ""


def section_hero(res: Results, hero_info) -> str:
    if not hero_info:
        return PENDING
    path, run, backend, d = hero_info
    return "\n".join(
        [
            f'<p align="center"><img src="{_link(path)}" alt="original vs received" width="820"></p>',
            "",
            f'<h2 align="center">{human(run["original_bytes"])} → {run["packet_bytes"]} bytes</h2>',
            "",
            f'<p align="center"><em>{run["image"]} sent as {run["packet_bytes"]} bytes of English '
            f"({run['ratio']:,.0f}× smaller), received via {backend} ({d['model']}). "
            f"Vibe Score: {vibe(d.get('vibe_score'))}. "
            f"SSIM: {num(d.get('ssim'), 2)}.</em></p>",
        ]
    )


def section_results(res: Results, grids: dict[str, Path]) -> str:
    if res.empty:
        return PENDING
    caps = captions()
    backends = res.backends()
    parts = []
    size_rows = []
    for image in res.images():
        for language in ("english", "terse"):
            r = res.get(image, language)
            if not r:
                continue
            s = r["sizes"]
            size_rows.append(
                [
                    f"`{image}`",
                    language,
                    human(r["original_bytes"]),
                    f"{s['none']:,}",
                    f"{s['zlib']:,}",
                    f"{s['brotli']:,}" if "brotli" in s else "–",
                    f"{s['text_b85']:,}",
                    f"**{r['ratio']:,.0f}×**",
                    f"{r['encode']['input_tokens']:,} / {r['encode']['output_tokens']:,}",
                ]
            )
    parts.append("#### Sizes\n")
    parts.append(
        table(
            [
                "Image",
                "Lang",
                "Original",
                "Packet (raw)",
                "zlib",
                "brotli",
                "`slop:` text",
                "Smaller by",
                "Encode tokens in/out",
            ],
            size_rows,
            "lllrrrrrr",
        )
    )
    parts.append(
        '\n_Packet sizes include SLOP\'s 16-byte header and checksum. "Smaller by" compares the '
        "original file to the smallest packet._\n"
    )

    fid_rows = []
    for r, backend, d in sorted(
        res.ok_decodes(), key=lambda x: (x[0]["image"], x[0]["language"], x[1])
    ):
        total = r["encode"]["seconds"] + d["seconds"]
        cost = r["encode"]["cost_usd"] + d["cost_usd"]
        fid_rows.append(
            [
                f"`{r['image']}`",
                r["language"],
                backend,
                f"**{vibe(d.get('vibe_score'))}**",
                num(d.get("ssim"), 3),
                num(d.get("clip"), 3),
                f"{d['input_tokens']:,}" if d.get("input_tokens") else "–",
                f"{r['encode']['seconds']:.1f} / {d['seconds']:.1f} / {total:.1f}",
                money(cost),
                f"~{d.get('joules_ish', 0) / 1000:,.1f} kJ",
            ]
        )
    parts.append("#### Fidelity, cost and latency\n")
    parts.append(
        table(
            [
                "Image",
                "Lang",
                "Backend",
                "Vibe",
                "SSIM",
                "CLIP",
                "Decode tokens in",
                "Latency enc/dec/total (s)",
                "Cost",
                "Joules-ish†",
            ],
            fid_rows,
            "lllrrrrrrr",
        )
    )
    clip_note = (
        ""
        if res.meta.get("clip")
        else " CLIP similarity shows – because `open_clip` was not installed for this run."
    )
    parts.append(
        "\n_Vibe Score: a vision model "
        f"({(res.meta.get('judge') or {}).get('model', 'n/a')}) compares the original and the "
        'received image and answers "is this the same picture, spiritually?" SSIM: pixel '
        "similarity, 1.0 is identical; expect it to be terrible, because SLOP does not transmit "
        f"pixels.{clip_note} Cost: estimated from reported token counts × the price table in "
        "`src/slop/config.py`._\n\n"
        "_† Joules-ish is a rough guess (fixed per-call constants in `config.py`), not a measurement. "
        "It is here to make you feel something._\n"
    )

    parts.append("#### Side by side\n")
    for image in res.images():
        stem = Path(image).stem
        grid = grids.get(image)
        if not grid:
            continue
        parts.append(f"**`{image}`**" + (f" — {caps[stem]}" if stem in caps else ""))
        parts.append("")
        parts.append(_img(grid, f"{image}: original and received"))
        english = res.get(image, "english")
        if english:
            snippet = english["description"].strip().split("\n")[0]
            if len(snippet) > 220:
                snippet = snippet[:217].rstrip() + "..."
            parts.append("")
            parts.append(f"> {esc(snippet)}")
        reasons = [
            f"{b}: _{esc(d['vibe_reason'])}_"
            for b, d in (english or {}).get("decodes", {}).items()
            if d.get("vibe_reason")
        ]
        if reasons:
            parts.append(">\n> Judge: " + " · ".join(reasons))
        parts.append("")
    if len(backends) == 1 and res.meta.get("skipped_backends"):
        skipped = ", ".join(f"{b} ({why})" for b, why in res.meta["skipped_backends"].items())
        parts.append(f"_Skipped backends for this run: {skipped}._\n")
    return "\n".join(parts)


def section_languages(res: Results) -> str:
    if res.empty:
        return PENDING
    rows = []
    eng_sizes, terse_sizes = [], []
    for image in res.images():
        e, t = res.get(image, "english"), res.get(image, "terse")
        if not (e and t):
            continue
        eng_sizes.append(e["packet_bytes"])
        terse_sizes.append(t["packet_bytes"])
        ev = mean(d.get("vibe_score") for d in e["decodes"].values() if "error" not in d)
        tv = mean(d.get("vibe_score") for d in t["decodes"].values() if "error" not in d)
        delta = (t["packet_bytes"] - e["packet_bytes"]) / e["packet_bytes"] * 100
        rows.append(
            [
                f"`{image}`",
                f"{e['packet_bytes']:,}",
                f"{t['packet_bytes']:,}",
                f"{delta:+.0f}%",
                num(ev, 1),
                num(tv, 1),
            ]
        )
    if not rows:
        return "_Only one language was benchmarked._"
    total_delta = (sum(terse_sizes) - sum(eng_sizes)) / sum(eng_sizes) * 100
    rows.append(
        [
            "**total**",
            f"**{sum(eng_sizes):,}**",
            f"**{sum(terse_sizes):,}**",
            f"**{total_delta:+.0f}%**",
            num(mean(float(r[4]) if r[4] != "–" else None for r in rows), 1),
            num(mean(float(r[5]) if r[5] != "–" else None for r in rows), 1),
        ]
    )
    return table(
        [
            "Image",
            "English packet (B)",
            "Terse packet (B)",
            "Change",
            "Mean vibe (English)",
            "Mean vibe (terse)",
        ],
        rows,
    )


def section_backends(res: Results) -> str:
    if res.empty:
        return PENDING
    by: dict[str, list[dict]] = defaultdict(list)
    models: dict[str, str] = {}
    for _r, backend, d in res.ok_decodes():
        by[backend].append(d)
        models[backend] = d["model"]
    rows = []
    for backend, ds in by.items():
        rows.append(
            [
                f"{backend} (`{models[backend]}`)",
                str(len(ds)),
                num(mean(d.get("vibe_score") for d in ds), 1),
                num(mean(d.get("ssim") for d in ds), 3),
                num(mean(d.get("clip") for d in ds), 3),
                num(mean(d["seconds"] for d in ds), 1, " s"),
                money(mean(d["cost_usd"] for d in ds)),
            ]
        )
    out = table(
        [
            "Backend",
            "Images",
            "Mean vibe",
            "Mean SSIM",
            "Mean CLIP",
            "Mean decode time",
            "Mean cost/image",
        ],
        rows,
    )
    skipped = res.meta.get("skipped_backends") or {}
    if skipped:
        out += (
            "\n\n_Not benchmarked: "
            + ", ".join(f"{b} ({why})" for b, why in skipped.items())
            + "._"
        )
    if len(by) == 1:
        out += (
            "\n\n_Only one image backend had a key for this run, so there is no head-to-head yet. "
            "Re-run `slop bench --backends openai,gemini` with both keys set._"
        )
    return out


def section_sweep(res: Results, strip: Path | None) -> str:
    rows = res.sweep()
    if not rows:
        return (
            PENDING
            if res.empty
            else "_No budget sweep in this run (`slop bench --sweep <image>`)._"
        )
    table_rows = []
    for r in rows:
        for backend, d in r.get("decodes", {}).items():
            if "error" in d:
                continue
            budget = "full detail" if r["budget"] is None else f"{r['budget']:,} B"
            notes = []
            if r["encode"]["attempts"] > 1:
                notes.append(f"{r['encode']['attempts']} attempts")
            if r["encode"]["truncated"]:
                notes.append("truncated")
            table_rows.append(
                [
                    budget,
                    f"{r['packet_bytes']:,}",
                    f"{r['ratio']:,.0f}×",
                    backend,
                    f"**{vibe(d.get('vibe_score'))}**",
                    num(d.get("ssim"), 3),
                    ", ".join(notes) or "–",
                ]
            )
            break
    parts = [
        f"Image: `{rows[0]['image']}`\n",
        table(
            ["Budget", "Packet", "Smaller by", "Backend", "Vibe", "SSIM", "Notes"],
            table_rows,
            "lrrlrrl",
        ),
        "",
        _img(strip, "budget sweep"),
        "",
    ]
    for r in rows:
        if r["budget"] is not None and r["budget"] <= 300:
            parts.append(f"The {r['budget']}-byte packet, in full:\n\n> {esc(r['description'])}\n")
    return "\n".join(parts)


def section_telephone(res: Results, strips: dict[str, Path]) -> str:
    games = res.telephone_games
    if not games:
        return (
            PENDING
            if res.empty
            else "_No telephone games in this run (`slop bench --telephone <image>`)._"
        )
    parts = []
    for key, game in games.items():
        rounds = game.get("rounds", [])
        if not rounds:
            continue
        strip = strips.get(key)
        scores = " → ".join(vibe(r.get("vibe_score")).replace("/10", "") for r in rounds)
        parts.append(
            f"**`{game['image']}`** via {game['backend']} (`{game['model']}`), {len(rounds)} rounds\n"
        )
        parts.append(_img(strip, f"telephone: {game['image']}"))
        parts.append(f"\nVibe vs. the original, by generation: {scores}\n")
        first = rounds[0]["description"].split("\n")[0][:200]
        last = rounds[-1]["description"].split("\n")[0][:200]
        parts.append(f"- Generation 1 opened with: _{esc(first)}_")
        parts.append(f"- Generation {rounds[-1]['index']} opened with: _{esc(last)}_\n")
    return "\n".join(parts) if parts else PENDING


def section_cost(res: Results) -> str:
    decodes = res.ok_decodes()
    if not decodes:
        return PENDING
    saved = sum(r["original_bytes"] - r["packet_bytes"] for r, _b, _d in decodes)
    dollars = sum(r["encode"]["cost_usd"] + d["cost_usd"] for r, _b, d in decodes)
    seconds = sum(r["encode"]["seconds"] + d["seconds"] for r, _b, d in decodes)
    sent = sum(r["packet_bytes"] for r, _b, _d in decodes)
    slop_bw = metrics.effective_bandwidth(saved, seconds, dollars)
    rows = []
    for link in [slop_bw, *metrics.reference_links()]:
        rows.append(
            [
                f"**{link.name}**" if link.name == "SLOP" else link.name,
                human(link.bytes_per_second) + "/s",
                f"{human(link.bytes_per_dollar)}/$"
                if link.bytes_per_dollar
                else (link.note or "–"),
            ]
        )
    intro = (
        f"Across {len(decodes)} transfers SLOP avoided sending **{human(saved)}** of pixels by "
        f"sending **{human(sent)}** of words instead. That cost an estimated **{money(dollars)}** "
        f"and **{seconds:,.0f} s** of waiting.\n\n"
        '"Effective bandwidth" here is bytes *not* sent per second waited and per dollar spent. '
        "It is the most flattering metric we could find.\n"
    )
    return (
        intro
        + "\n"
        + table(["Link", "Effective throughput", "Bytes per dollar"], rows, "lrr")
        + "\n\n_Modem: 53 kbit/s. Pigeon: 1 TB microSD, 50 km at 80 km/h, $0.05 of birdseed. "
        "Constants in `src/slop/config.py`._"
    )


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def replace_section(text: str, name: str, body: str) -> str:
    pattern = re.compile(rf"(<!-- slop:{name} -->)(.*?)(<!-- /slop:{name} -->)", re.DOTALL)
    if not pattern.search(text):
        return text
    return pattern.sub(lambda m: f"{m.group(1)}\n{body.strip()}\n{m.group(3)}", text)


def build(
    results_path: Path = Path("benchmarks/results/results.json"),
    *,
    outputs_dir: Path = Path("examples/outputs"),
    originals: Path = Path("examples/originals"),
    readme: Path | None = README,
) -> list[Path]:
    global _BASE
    _BASE = readme.parent if readme else Path(".")
    res = Results.load(results_path)
    written: list[Path] = []
    grids_dir = outputs_dir / "grids"
    grids = {}
    for image in res.images():
        g = comparison_grid(res, image, originals, grids_dir / f"{Path(image).stem}.png")
        if g:
            grids[image] = g
            written.append(g)
    hero_info = hero(res, originals, outputs_dir / "hero.png")
    if hero_info:
        written.append(hero_info[0])
    strip = sweep_strip(res, originals, grids_dir / "sweep.png")
    if strip:
        written.append(strip)
    strips = {}
    for key, game in res.telephone_games.items():
        stem = Path(game["image"]).stem
        s = telephone_strip(game, originals, outputs_dir / "telephone" / stem / "filmstrip.png")
        if s:
            strips[key] = s
            written.append(s)

    sections = {
        "hero": section_hero(res, hero_info),
        "results": section_results(res, grids),
        "languages": section_languages(res),
        "backends": section_backends(res),
        "sweep": section_sweep(res, strip),
        "telephone": section_telephone(res, strips),
        "cost": section_cost(res),
    }
    summary = results_path.parent / "summary.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    meta = res.meta
    header = [
        "# SLOP benchmark summary",
        "",
        f"Generated from `{results_path.as_posix()}` (updated {meta.get('updated', 'n/a')}).",
        f"Encoder: {meta.get('encoder')}. Judge: {meta.get('judge')}. Backends: {meta.get('backends')}.",
        "",
    ]
    body = [f"## {name.title()}\n\n{text}\n" for name, text in sections.items()]
    summary.write_text("\n".join(header + body))
    written.append(summary)

    if readme and readme.exists():
        text = readme.read_text()
        for name, body_text in sections.items():
            text = replace_section(text, name, body_text)
        readme.write_text(text)
        written.append(readme)
    return written


def main() -> None:  # pragma: no cover - convenience entry point
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate benchmark tables and README sections.")
    parser.add_argument("results", nargs="?", default="benchmarks/results/results.json")
    args = parser.parse_args()
    for path in build(Path(args.results)):
        print("wrote", path)


if __name__ == "__main__":  # pragma: no cover
    main()
