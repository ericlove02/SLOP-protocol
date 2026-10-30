"""The ``slop`` command line."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from slop import __version__, config
from slop.encoder import BudgetError, encode_detailed
from slop.packet import MAGIC, TEXT_PREFIX, TEXT_PREFIX_B64, Packet, PacketError
from slop.providers import ProviderError, available

app = typer.Typer(
    name="slop",
    help="SLOP: the Semantically Lossy Object Protocol. It's a picture of a thing. Probably.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
out = Console()
err = Console(stderr=True)


@contextlib.contextmanager
def errors() -> Iterator[None]:
    """Turn expected failures into one red line and exit code 1."""
    try:
        yield
    except (ProviderError, PacketError, BudgetError, ConnectionError, FileNotFoundError) as exc:
        err.print(f"[bold red]slop:[/] {exc}")
        raise typer.Exit(1) from exc


def _human(n: int | float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover


def _parse_budget(value: str | None) -> int | None:
    if value is None:
        return None
    if value in config.BUDGETS:
        return config.BUDGETS[value]
    try:
        return int(value)
    except ValueError:
        raise typer.BadParameter(
            f"use a number of bytes or one of: {', '.join(config.BUDGETS)}"
        ) from None


def _parse_size(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise typer.BadParameter("size must look like 1024x768") from None


def _looks_like_packet(path: Path) -> bool:
    head = path.read_bytes()[:8]
    return head.startswith(MAGIC) or head.startswith(
        (TEXT_PREFIX.encode(), TEXT_PREFIX_B64.encode())
    )


Vision = Annotated[
    str,
    typer.Option("--vision", "-v", help="Encoder: auto, anthropic, openai, gemini, openai_compat."),
]
Backend = Annotated[
    str,
    typer.Option(
        "--backend", "-b", help="Decoder: auto, openai, gemini, stability, replicate, local."
    ),
]
Terse = Annotated[
    bool, typer.Option("--terse", help="Telegraphic English: no articles, more detail per byte.")
]
MaxBytes = Annotated[
    str | None,
    typer.Option(
        "--max-bytes",
        help="Packet budget in bytes, or a name: tweet (280), sms (160), kilobyte, floppy-sector.",
    ),
]
MaxChars = Annotated[
    int | None, typer.Option("--max-chars", help="Budget for the slop: text form, in characters.")
]
Seed = Annotated[int | None, typer.Option("--seed", help="Seed, where the backend supports it.")]
Model = Annotated[str | None, typer.Option("--model", help="Override the provider's model.")]


def _encode(
    image: Path,
    *,
    vision: str,
    model: str | None,
    terse: bool,
    max_bytes: str | None,
    max_chars: int | None,
    compression: str = "auto",
):
    with err.status(f"Describing {image.name} in exhaustive detail..."):
        return encode_detailed(
            image,
            vision=vision,
            model=model,
            terse=terse,
            max_bytes=_parse_budget(max_bytes),
            max_chars=max_chars,
            compression=compression,
        )


def _encode_summary(result) -> str:
    raw = len(result.packet.to_bytes())
    ratio = f" ({result.original_bytes / raw:,.0f}x smaller)" if result.original_bytes else ""
    notes = []
    if result.attempts > 1:
        notes.append(f"{result.attempts} attempts")
    if result.truncated:
        notes.append("truncated to fit")
    note = f" [dim]({', '.join(notes)})[/]" if notes else ""
    return (
        f"[bold]{_human(result.original_bytes)} -> {raw} bytes[/]{ratio}{note}  "
        f"[dim]~${result.cost_usd:.4f}, {result.seconds:.1f}s[/]"
    )


# --------------------------------------------------------------------------
# encode / decode / inspect
# --------------------------------------------------------------------------


@app.command()
def encode(
    image: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PNG/JPEG/WebP.")],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Where to write the .slop packet.")
    ] = None,
    vision: Vision = "auto",
    model: Model = None,
    terse: Terse = False,
    max_bytes: MaxBytes = None,
    max_chars: MaxChars = None,
    compression: Annotated[str, typer.Option(help="auto (smallest), none, zlib, brotli.")] = "auto",
    text: Annotated[bool, typer.Option("--text", help="Also print the slop: text form.")] = False,
) -> None:
    """Turn an image into a SLOP packet (via a vision model)."""
    with errors():
        result = _encode(
            image,
            vision=vision,
            model=model,
            terse=terse,
            max_bytes=max_bytes,
            max_chars=max_chars,
            compression=compression,
        )
        output = output or image.with_suffix(".slop")
        result.packet.save(output)
        err.print(f"[green]wrote[/] {output}  " + _encode_summary(result))
        if text:
            out.print(result.packet.to_text(), soft_wrap=True)


@app.command()
def decode(
    packet: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="A .slop packet or slop: text file.")
    ],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Where to write the image.")
    ] = None,
    backend: Backend = "auto",
    model: Model = None,
    seed: Seed = None,
    size: Annotated[
        str | None, typer.Option(help="Override the size hint, e.g. 1024x1024.")
    ] = None,
) -> None:
    """Turn a SLOP packet back into an image. An image. Not necessarily the image."""
    from slop.decoder import decode_detailed

    with errors():
        with err.status("Imagining..."):
            result = decode_detailed(
                packet, backend=backend, model=model, seed=seed, size=_parse_size(size)
            )
        output = output or packet.with_suffix(".png")
        result.save(output)
        seed_note = ""
        if seed is not None and not result.seed_honoured:
            seed_note = f" [yellow](note: {result.backend} ignores seeds)[/]"
        err.print(
            f"[green]wrote[/] {output}  [dim]{result.backend}/{result.model}, "
            f"~${result.cost_usd:.4f}, {result.seconds:.1f}s[/]{seed_note}"
        )


@app.command()
def inspect(
    packet: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Pretty-print a packet's header, sizes, and the description inside."""
    with errors():
        raw = packet.read_bytes()
        p = Packet.load(raw)
        wire = p.to_bytes()
        info = {
            "file": str(packet),
            "file_bytes": len(raw),
            "version": p.version,
            "compression": p.compression.name.lower(),
            "terse": p.terse,
            "size_hint": [p.width, p.height],
            "body_bytes": len(wire) - 16,
            "crc32": wire[-4:].hex(),
            "sizes": p.sizes(),
            "description": p.description,
        }
        if as_json:
            out.print_json(json.dumps(info))
            return
        header = Table(title=f"SLOP packet: {packet.name}", show_header=False, box=None)
        header.add_row("version", str(p.version))
        header.add_row("compression", info["compression"])
        header.add_row("terse", "yes" if p.terse else "no")
        header.add_row("size hint", f"{p.width}x{p.height}" if p.width else "receiver's choice")
        header.add_row("body", f"{info['body_bytes']} bytes")
        header.add_row("crc32", info["crc32"])
        out.print(header)
        sizes = Table(title="Wire sizes", show_header=True)
        sizes.add_column("form")
        sizes.add_column("bytes", justify="right")
        for key, value in info["sizes"].items():
            sizes.add_row(key, f"{value:,}")
        out.print(sizes)
        out.print(Panel(p.description, title="description", expand=False))


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def _packet_from_input(
    source: Path, vision: str, model: str | None, terse: bool, max_bytes: str | None
) -> Packet:
    if _looks_like_packet(source):
        return Packet.load(source)
    result = _encode(
        source, vision=vision, model=model, terse=terse, max_bytes=max_bytes, max_chars=None
    )
    err.print("encoded  " + _encode_summary(result))
    return result.packet


@app.command()
def send(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, help="An image (encoded first) or a .slop packet."
        ),
    ],
    to: Annotated[
        str | None, typer.Option("--to", help="HTTP receiver URL, e.g. http://host:8080")
    ] = None,
    tcp: Annotated[str | None, typer.Option("--tcp", help="TCP receiver, host:port")] = None,
    vision: Vision = "auto",
    model: Model = None,
    terse: Terse = False,
    max_bytes: MaxBytes = None,
    as_text: Annotated[
        bool, typer.Option("--as-text", help="Send the slop: text form over HTTP.")
    ] = False,
) -> None:
    """Send an image to a SLOP receiver as a description of itself."""
    if bool(to) == bool(tcp):
        raise typer.BadParameter("give exactly one of --to URL or --tcp host:port")
    with errors():
        packet = _packet_from_input(source, vision, model, terse, max_bytes)
        size = len(packet.to_bytes())
        if to:
            from slop.transport import http

            with err.status(f"POSTing {size} bytes to {to} (the receiver is now imagining)..."):
                receipt = http.send(packet, to, text=as_text)
            err.print(f"[green]delivered[/] {size} bytes")
            out.print_json(json.dumps(receipt))
        else:
            from slop.transport import tcp as tcp_mod

            host, port = tcp_mod.parse_address(tcp or "")
            reply = tcp_mod.send(packet, host, port)
            err.print(f"[green]delivered[/] {size} bytes over TCP: {reply}")


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8080,
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    out_dir: Annotated[Path, typer.Option("--out", help="Inbox directory.")] = Path("inbox"),
    backend: Backend = "auto",
    seed: Seed = None,
    no_decode: Annotated[
        bool, typer.Option("--no-decode", help="Store packets without generating images.")
    ] = False,
) -> None:
    """Run an HTTP receiver that turns incoming packets into images."""
    from slop.transport import http

    config.load_env()
    server = http.make_server(host, port, out_dir, decode=not no_decode, backend=backend, seed=seed)
    err.print(
        f"[bold]SLOP/1 receiver[/] on http://{host}:{port}  inbox: {out_dir}/  (Ctrl-C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        err.print("bye")
    finally:
        server.server_close()


@app.command()
def listen(
    tcp: Annotated[str, typer.Option("--tcp", help="Port, or host:port, to listen on.")] = "5150",
    out_dir: Annotated[Path, typer.Option("--out", help="Inbox directory.")] = Path("inbox"),
    backend: Backend = "auto",
    seed: Seed = None,
    no_decode: Annotated[
        bool, typer.Option("--no-decode", help="Store packets without generating images.")
    ] = False,
) -> None:
    """Run a raw TCP receiver, for maximum retro."""
    from slop.transport import tcp as tcp_mod

    config.load_env()
    host, port = tcp_mod.parse_address(tcp, default_host="0.0.0.0")
    server = tcp_mod.make_server(
        host, port, out_dir, decode=not no_decode, backend=backend, seed=seed
    )
    err.print(f"[bold]SLOP/1 TCP receiver[/] on {host}:{port}  inbox: {out_dir}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        err.print("bye")
    finally:
        server.server_close()


@app.command()
def qr(
    source: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="An image or a .slop packet.")
    ],
    output: Annotated[Path | None, typer.Option("-o", "--output")] = None,
    vision: Vision = "auto",
    model: Model = None,
    terse: Terse = False,
    max_bytes: MaxBytes = None,
) -> None:
    """Encode an image as a QR code of its own description."""
    from slop.transport.qr import packet_to_qr

    with errors():
        packet = _packet_from_input(source, vision, model, terse, max_bytes)
        image = packet_to_qr(packet)
        output = output or source.with_name(source.stem + "_qr.png")
        image.save(output)
        err.print(
            f"[green]wrote[/] {output}  "
            f"[dim]({image.width}x{image.height} QR of {len(packet.to_bytes())} bytes)[/]"
        )


@app.command()
def unqr(
    image: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="A photo or PNG of a SLOP QR code.")
    ],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Where to write the decoded image.")
    ] = None,
    packet_out: Annotated[
        Path | None, typer.Option("--packet", help="Also save the .slop packet here.")
    ] = None,
    backend: Backend = "auto",
    model: Model = None,
    seed: Seed = None,
    no_decode: Annotated[
        bool, typer.Option("--no-decode", help="Only extract the packet.")
    ] = False,
) -> None:
    """Read a SLOP QR code and decode it back into an image."""
    from PIL import Image

    from slop.decoder import decode_detailed
    from slop.transport.qr import qr_to_packet

    with errors():
        packet = qr_to_packet(Image.open(image))
        err.print(f"read {len(packet.to_bytes())}-byte packet from QR")
        if packet_out:
            packet.save(packet_out)
            err.print(f"[green]wrote[/] {packet_out}")
        if no_decode:
            out.print(Panel(packet.description, title="description", expand=False))
            return
        with err.status("Imagining..."):
            result = decode_detailed(packet, backend=backend, model=model, seed=seed)
        output = output or image.with_name(image.stem + "_decoded.png")
        result.save(output)
        err.print(f"[green]wrote[/] {output}  [dim]{result.backend}/{result.model}[/]")


# --------------------------------------------------------------------------
# games and science
# --------------------------------------------------------------------------


@app.command()
def telephone(
    image: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    rounds: Annotated[int, typer.Option(help="How many encode->decode trips.")] = 10,
    out_dir: Annotated[
        Path | None, typer.Option("--out", help="Directory for every generation.")
    ] = None,
    vision: Vision = "auto",
    backend: Backend = "auto",
    terse: Terse = False,
    max_bytes: MaxBytes = None,
    seed: Seed = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the cost confirmation.")] = False,
) -> None:
    """Play telephone: encode, decode, re-encode the output, repeat. Save every generation."""
    from PIL import Image

    from slop import telephone as tel

    out_dir = out_dir or Path("telephone") / image.stem
    if not yes:
        typer.confirm(
            f"This makes {rounds} vision calls and {rounds} image generations "
            f"(roughly ${rounds * 0.06:.2f}). Continue?",
            abort=True,
        )
    with errors():

        def report(r: tel.Round) -> None:
            err.print(
                f"gen {r.index:2d}: {len(r.encoded.packet.to_bytes()):5d} B  "
                f"[dim]{r.description[:90].replace(chr(10), ' ')}...[/]"
            )

        history = tel.play(
            image,
            rounds,
            vision=vision,
            backend=backend,
            terse=terse,
            max_bytes=_parse_budget(max_bytes),
            seed=seed,
            out_dir=out_dir,
            on_round=report,
        )
        strip_path = out_dir / "filmstrip.png"
        tel.strip(Image.open(image), history, caption=f"Telephone: {image.name}").save(strip_path)
        err.print(f"[green]wrote[/] {strip_path}")


@app.command()
def bench(
    originals: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Directory of images.")
    ] = Path("examples/originals"),
    backends: Annotated[
        str, typer.Option(help="Comma-separated decoders, e.g. openai,gemini.")
    ] = "openai,gemini",
    vision: Vision = "auto",
    judge: Annotated[
        str, typer.Option(help="Vision provider for the Vibe Score (or 'none').")
    ] = "auto",
    languages: Annotated[
        str, typer.Option(help="Comma-separated: english,terse.")
    ] = "english,terse",
    sweep: Annotated[
        str | None, typer.Option(help="Budget sweep on this image filename (stem).")
    ] = None,
    sweep_budgets: Annotated[
        str, typer.Option(help="Budgets for the sweep.")
    ] = "none,1024,280,160",
    telephone_images: Annotated[
        str,
        typer.Option(
            "--telephone", help="Comma-separated stems to play telephone with ('' to skip)."
        ),
    ] = "",
    telephone_rounds: Annotated[int, typer.Option(help="Rounds per telephone game.")] = 10,
    results_dir: Annotated[Path, typer.Option("--results", help="Where results go.")] = Path(
        "benchmarks/results"
    ),
    outputs_dir: Annotated[
        Path, typer.Option("--outputs", help="Where generated images go.")
    ] = Path("examples/outputs"),
    limit: Annotated[int | None, typer.Option(help="Only the first N images.")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan and cost estimate, then stop.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the cost confirmation.")] = False,
) -> None:
    """Benchmark SLOP on a directory of images. Spends real money; asks first."""
    from slop.bench import run as bench_run

    with errors():
        plan = bench_run.Plan.build(
            originals=originals,
            backends=[b for b in backends.split(",") if b],
            languages=[lang for lang in languages.split(",") if lang],
            vision=vision,
            judge=judge,
            sweep=sweep,
            sweep_budgets=sweep_budgets.split(","),
            telephone=[t for t in telephone_images.split(",") if t],
            telephone_rounds=telephone_rounds,
            limit=limit,
        )
        err.print(plan.describe())
        if dry_run:
            return
        if not yes:
            typer.confirm("Spend that?", abort=True)
        bench_run.execute(plan, results_dir=results_dir, outputs_dir=outputs_dir, console=err)


@app.command()
def doctor() -> None:
    """Show which providers are usable (key names only; values are never printed)."""
    config.load_env()
    avail = available()
    table = Table(title="SLOP providers")
    table.add_column("role")
    table.add_column("provider")
    table.add_column("ready")
    from slop.providers import IMAGEGEN, VISION

    for role, names, ready in (
        ("encoder", VISION, avail["vision"]),
        ("decoder", IMAGEGEN, avail["imagegen"]),
    ):
        for name in names:
            table.add_row(role, name, "[green]key found[/]" if name in ready else "[dim]no key[/]")
    out.print(table)
    if not avail["vision"]:
        err.print(
            "[yellow]No encoder key: set ANTHROPIC_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY).[/]"
        )
    if not avail["imagegen"]:
        err.print(
            "[yellow]No decoder key: set OPENAI_API_KEY or GEMINI_API_KEY. "
            "Claude can read images but cannot draw them.[/]"
        )


def _version(value: bool) -> None:
    if value:
        out.print(f"slop {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    """SLOP: the Semantically Lossy Object Protocol."""
