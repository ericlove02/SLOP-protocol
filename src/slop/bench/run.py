"""Benchmark harness.

Runs every image through encode -> decode for each language and backend, then
optionally a budget sweep and a game of telephone. Results are written to JSON
after every step, and a re-run resumes instead of paying twice.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image
from rich.console import Console
from rich.markup import escape

from slop import __version__, config
from slop.bench import metrics
from slop.decoder import decode_detailed
from slop.encoder import encode_detailed, load_image
from slop.packet import Packet
from slop.providers import (
    MissingKeyError,
    get_imagegen,
    get_vision,
    has_key,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
RESULTS_FILE = "results.json"

# Rough per-call usage for the pre-flight estimate (measured runs replace these).
EST_ENCODE_TOKENS = (1_800, 700)
EST_JUDGE_TOKENS = (3_400, 80)

_VISION_MODELS = {
    "anthropic": config.ANTHROPIC_VISION_MODEL,
    "openai": config.OPENAI_VISION_MODEL,
    "gemini": config.GEMINI_VISION_MODEL,
    "openai_compat": config.OPENAI_COMPAT_MODEL,
}
_IMAGE_MODELS = {
    "openai": config.OPENAI_IMAGE_MODEL,
    "gemini": config.GEMINI_IMAGE_MODEL,
    "stability": config.STABILITY_IMAGE_MODEL,
    "replicate": config.REPLICATE_IMAGE_MODEL,
    "local": config.LOCAL_IMAGE_MODEL,
}


def _model(table: dict, provider: str) -> str:
    entry = table.get(provider)
    return entry.get() if entry else provider


def _resolve_vision_name(name: str) -> str:
    if name != "auto":
        return name
    for candidate in config.VISION_AUTO_ORDER:
        if has_key(candidate):
            return candidate
    raise MissingKeyError(
        "the benchmark needs an encoder key: ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY"
    )


def _parse_budget(value: str) -> int | None:
    value = value.strip().lower()
    if value in ("none", "full", ""):
        return None
    return config.BUDGETS.get(value) or int(value)


def _rel(path: Path) -> str:
    try:
        return os.path.relpath(path, Path.cwd())
    except ValueError:  # different drive on Windows
        return str(path)


@dataclass
class Plan:
    images: list[Path]
    backends: list[str]
    languages: list[str]
    vision: str
    judge: str | None
    sweep: Path | None = None
    sweep_budgets: list[int | None] = field(default_factory=list)
    telephone: list[Path] = field(default_factory=list)
    telephone_rounds: int = 10
    skipped: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        originals: Path,
        backends: list[str],
        languages: list[str],
        vision: str = "auto",
        judge: str = "auto",
        sweep: str | None = None,
        sweep_budgets: list[str] | None = None,
        telephone: list[str] | None = None,
        telephone_rounds: int = 10,
        limit: int | None = None,
    ) -> Plan:
        config.load_env()
        images = sorted(p for p in originals.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        by_stem = {p.stem: p for p in images}
        if limit:
            images = images[:limit]
        if not images:
            raise FileNotFoundError(f"no images in {originals}")

        def find(stem: str) -> Path:
            if stem in by_stem:
                return by_stem[stem]
            matches = [p for s, p in by_stem.items() if stem in s]
            if len(matches) != 1:
                raise FileNotFoundError(f"cannot find a single image matching {stem!r}")
            return matches[0]

        skipped: dict[str, str] = {}
        usable = []
        for backend in backends:
            if backend == "local" or has_key(backend):
                usable.append(backend)
            else:
                skipped[backend] = "no API key in the environment"
        if not usable:
            raise MissingKeyError(
                "no requested decoder has a key ("
                + ", ".join(f"{b}: {r}" for b, r in skipped.items())
                + "). Set OPENAI_API_KEY or GEMINI_API_KEY."
            )
        vision_name = _resolve_vision_name(vision)
        judge_name = None if judge == "none" else _resolve_vision_name(judge)
        return cls(
            images=images,
            backends=usable,
            languages=languages,
            vision=vision_name,
            judge=judge_name,
            sweep=find(sweep) if sweep else None,
            sweep_budgets=[_parse_budget(b) for b in (sweep_budgets or [])] if sweep else [],
            telephone=[find(t) for t in (telephone or [])],
            telephone_rounds=telephone_rounds,
            skipped=skipped,
        )

    # ------------------------------------------------------------------ cost

    def counts(self) -> dict[str, int]:
        matrix = len(self.images) * len(self.languages)
        sweep_extra = sum(1 for b in self.sweep_budgets if b is not None)
        tel = len(self.telephone) * self.telephone_rounds
        encodes = matrix + sweep_extra + tel
        decodes_by_backend = {b: matrix for b in self.backends}
        decodes_by_backend[self.backends[0]] += sweep_extra + tel
        judges = (matrix * len(self.backends) + sweep_extra + tel) if self.judge else 0
        return {
            "encodes": encodes,
            "judges": judges,
            **{f"decode:{b}": n for b, n in decodes_by_backend.items()},
        }

    def estimate(self) -> float:
        table = config.prices()
        counts = self.counts()

        def vision_cost(provider: str, tokens: tuple[int, int]) -> float:
            price = table.get(_model(_VISION_MODELS, provider), {})
            return (tokens[0] * price.get("input", 0) + tokens[1] * price.get("output", 0)) / 1e6

        total = counts["encodes"] * vision_cost(self.vision, EST_ENCODE_TOKENS)
        if self.judge:
            total += counts["judges"] * vision_cost(self.judge, EST_JUDGE_TOKENS)
        for backend in self.backends:
            price = table.get(_model(_IMAGE_MODELS, backend), {})
            total += counts[f"decode:{backend}"] * price.get("per_image", 0.05)
        return total

    def describe(self) -> str:
        c = self.counts()
        decode_lines = ", ".join(
            f"{c[f'decode:{b}']} x {b} ({_model(_IMAGE_MODELS, b)})" for b in self.backends
        )
        lines = [
            "[bold]SLOP benchmark plan[/]",
            f"  images:     {len(self.images)} ({', '.join(p.name for p in self.images)})",
            f"  languages:  {', '.join(self.languages)}",
            f"  encoder:    {self.vision} ({_model(_VISION_MODELS, self.vision)})",
            f"  judge:      {self.judge or 'none'}",
            f"  decoders:   {', '.join(self.backends)}",
        ]
        if self.skipped:
            lines.append(
                "  [yellow]skipped:    "
                + ", ".join(f"{b} ({r})" for b, r in self.skipped.items())
                + "[/]"
            )
        if self.sweep:
            lines.append(
                f"  sweep:      {self.sweep.name} at "
                + ", ".join("full" if b is None else f"{b} B" for b in self.sweep_budgets)
            )
        if self.telephone:
            lines.append(
                f"  telephone:  {', '.join(p.name for p in self.telephone)} x {self.telephone_rounds} rounds"
            )
        lines += [
            "",
            f"  API calls:  {c['encodes']} encodes, {decode_lines}, {c['judges']} vibe judgements",
            f"  [bold]estimated cost: ~${self.estimate():.2f}[/] (prices in slop/config.py are estimates;"
            " already-completed steps in an existing results.json are skipped)",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


class Runner:
    def __init__(self, plan: Plan, results_dir: Path, outputs_dir: Path, console: Console):
        self.plan = plan
        self.results_dir = results_dir
        self.outputs_dir = outputs_dir
        self.packets_dir = outputs_dir.parent / "packets"
        self.console = console
        self.path = results_dir / RESULTS_FILE
        self.results = self._load()
        self.vision = get_vision(plan.vision)
        self.judge = get_vision(plan.judge) if plan.judge else None
        self.gens = {b: get_imagegen(b) for b in plan.backends}

    # -------------------------------------------------------------- storage

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"meta": {}, "runs": {}, "telephone": {}}

    def save(self) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.results, indent=2, sort_keys=False) + "\n")
        tmp.replace(self.path)

    def _meta(self) -> None:
        meta = self.results.setdefault("meta", {})
        meta.update(
            {
                "updated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "slop_version": __version__,
                "python": platform.python_version(),
                "encoder": {"provider": self.vision.name, "model": self.vision.model},
                "judge": (
                    {"provider": self.judge.name, "model": self.judge.model} if self.judge else None
                ),
                "clip": metrics.clip_available(),
                "prices_usd": config.prices(),
            }
        )
        backends = meta.setdefault("backends", {})
        for name, gen in self.gens.items():
            backends[name] = gen.model
        skipped = meta.setdefault("skipped_backends", {})
        skipped.update(self.plan.skipped)
        for name in self.gens:
            skipped.pop(name, None)

    # -------------------------------------------------------------- steps

    def _judge(self, original: Image.Image, received: Image.Image) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ssim": round(metrics.ssim(original, received), 4),
            "clip": None,
        }
        clip = metrics.clip_similarity(original, received)
        if clip is not None:
            out["clip"] = round(clip, 4)
        if self.judge:
            try:
                vibe = metrics.vibe_score(self.judge, original, received)
                out["vibe_score"] = vibe.score
                out["vibe_reason"] = vibe.reason
                out["judge_usage"] = vibe.usage.to_dict() if vibe.usage else None
            except Exception as exc:
                out["vibe_error"] = f"{type(exc).__name__}: {exc}"
        return out

    def _encode(self, key: str, image_path: Path, language: str, budget: int | None) -> dict | None:
        runs = self.results["runs"]
        entry = runs.get(key)
        if entry and "error" not in entry.get("encode", {}) and Path(entry["packet_file"]).exists():
            return entry
        stem = image_path.stem
        suffix = "" if budget is None else f".{budget}B"
        packet_file = self.packets_dir / f"{stem}.{language}{suffix}.slop"
        self.console.print(f"  encode {image_path.name} ({language}{suffix})")
        try:
            result = encode_detailed(
                image_path, vision=self.vision, terse=language == "terse", max_bytes=budget
            )
        except Exception as exc:
            runs[key] = {
                "image": image_path.name,
                "encode": {"error": f"{type(exc).__name__}: {exc}"},
            }
            self.console.print(f"  [red]encode failed:[/] {escape(str(exc))}")
            self.save()
            return None
        packet = result.packet
        packet.save(packet_file)
        original_image, original_bytes = load_image(image_path)
        sizes = packet.sizes()
        entry = {
            "image": image_path.name,
            "stem": stem,
            "language": language,
            "budget": budget,
            "original_bytes": original_bytes,
            "original_size": list(original_image.size),
            "packet_file": _rel(packet_file),
            "packet_bytes": len(packet.to_bytes()),
            "compression": packet.compression.name.lower(),
            "sizes": sizes,
            "ratio": round(metrics.ratio(original_bytes or 0, len(packet.to_bytes())), 1),
            "description": packet.description,
            "encode": {
                "provider": self.vision.name,
                "model": self.vision.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "seconds": round(result.seconds, 2),
                "cost_usd": round(result.cost_usd, 6),
                "attempts": result.attempts,
                "truncated": result.truncated,
            },
            "decodes": {},
        }
        runs[key] = entry
        self.save()
        return entry

    def _decode(self, entry: dict, backend: str, out_path: Path) -> None:
        decodes = entry.setdefault("decodes", {})
        existing = decodes.get(backend)
        if existing and "error" not in existing and Path(existing["image_file"]).exists():
            return
        gen = self.gens[backend]
        self.console.print(f"    decode -> {backend}")
        packet = Packet.load(Path(entry["packet_file"]))
        try:
            result = decode_detailed(packet, backend=gen)
        except Exception as exc:
            decodes[backend] = {"error": f"{type(exc).__name__}: {exc}"}
            self.console.print(f"    [red]{backend} failed:[/] {escape(str(exc))}")
            self.save()
            return
        result.save(out_path)
        original, _ = load_image(self.plan_image(entry["image"]))
        record: dict[str, Any] = {
            "model": gen.model,
            "image_file": _rel(out_path),
            "output_size": list(result.image.size),
            "input_tokens": sum(u.input_tokens for u in result.usage),
            "output_tokens": sum(u.output_tokens for u in result.usage),
            "seconds": round(result.seconds, 2),
            "cost_usd": round(result.cost_usd, 6),
        }
        record.update(self._judge(original, result.image))
        record["joules_ish"] = metrics.joules_ish(entry["encode"]["attempts"], 1)
        decodes[backend] = record
        self.save()

    def plan_image(self, name: str) -> Path:
        for p in (
            self.plan.images + self.plan.telephone + ([self.plan.sweep] if self.plan.sweep else [])
        ):
            if p.name == name:
                return p
        raise FileNotFoundError(name)

    # -------------------------------------------------------------- phases

    def matrix(self) -> None:
        for image_path in self.plan.images:
            for language in self.plan.languages:
                key = f"{image_path.stem}|{language}|full"
                entry = self._encode(key, image_path, language, None)
                if entry is None:
                    continue
                entry.setdefault("kinds", [])
                if "matrix" not in entry["kinds"]:
                    entry["kinds"].append("matrix")
                for backend in self.plan.backends:
                    out = self.outputs_dir / f"{image_path.stem}.{language}.{backend}.png"
                    self._decode(entry, backend, out)

    def sweep(self) -> None:
        if not self.plan.sweep:
            return
        backend = self.plan.backends[0]
        image_path = self.plan.sweep
        self.console.print(f"[bold]budget sweep[/] on {image_path.name}")
        for budget in self.plan.sweep_budgets:
            label = "full" if budget is None else f"{budget}B"
            key = f"{image_path.stem}|english|{label}"
            entry = self._encode(key, image_path, "english", budget)
            if entry is None:
                continue
            entry.setdefault("kinds", [])
            if "sweep" not in entry["kinds"]:
                entry["kinds"].append("sweep")
            suffix = "" if budget is None else f".{budget}B"
            out = self.outputs_dir / f"{image_path.stem}.english{suffix}.{backend}.png"
            self._decode(entry, backend, out)

    def telephone(self) -> None:
        backend = self.plan.backends[0]
        gen = self.gens[backend]
        for image_path in self.plan.telephone:
            key = f"{image_path.stem}|{backend}"
            game = self.results["telephone"].setdefault(
                key,
                {"image": image_path.name, "backend": backend, "model": gen.model, "rounds": []},
            )
            out_dir = self.outputs_dir / "telephone" / image_path.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            original, _ = load_image(image_path)
            original = original.convert("RGB")
            rounds = game["rounds"]
            current = original
            if rounds:
                current = Image.open(rounds[-1]["image_file"]).convert("RGB")
            self.console.print(f"[bold]telephone[/] {image_path.name} via {backend}")
            for index in range(len(rounds) + 1, self.plan.telephone_rounds + 1):
                try:
                    enc = encode_detailed(current, vision=self.vision)
                    dec = decode_detailed(enc.packet, backend=gen)
                except Exception as exc:
                    self.console.print(f"  [red]round {index} failed:[/] {escape(str(exc))}")
                    game["error"] = f"round {index}: {type(exc).__name__}: {exc}"
                    self.save()
                    break
                image_file = out_dir / f"gen{index:02d}.png"
                dec.save(image_file)
                enc.packet.save(out_dir / f"gen{index:02d}.slop")
                record = {
                    "index": index,
                    "image_file": _rel(image_file),
                    "packet_bytes": len(enc.packet.to_bytes()),
                    "description": enc.packet.description,
                    "encode_cost_usd": round(enc.cost_usd, 6),
                    "decode_cost_usd": round(dec.cost_usd, 6),
                    "seconds": round(enc.seconds + dec.seconds, 2),
                }
                record.update(self._judge(original, dec.image))
                rounds.append(record)
                game.pop("error", None)
                self.console.print(
                    f"  gen {index:2d}: {record['packet_bytes']} B, vibe {record.get('vibe_score')}"
                )
                self.save()
                current = dec.image


def execute(
    plan: Plan,
    *,
    results_dir: Path = Path("benchmarks/results"),
    outputs_dir: Path = Path("examples/outputs"),
    console: Console | None = None,
) -> dict[str, Any]:
    console = console or Console(stderr=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    runner = Runner(plan, results_dir, outputs_dir, console)
    runner._meta()
    runner.save()
    console.print("[bold]matrix[/]")
    runner.matrix()
    runner.sweep()
    runner.telephone()
    runner._meta()
    runner.save()

    from slop.bench import report

    written = report.build(runner.path, outputs_dir=outputs_dir)
    for path in written:
        console.print(f"[green]wrote[/] {path}")
    return runner.results
