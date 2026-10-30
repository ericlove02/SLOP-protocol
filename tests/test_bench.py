import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from conftest import FakeImageGen, FakeJudge
from slop import providers
from slop.bench import metrics, report
from slop.bench import run as bench_run
from slop.cli import app

README_TEMPLATE = "\n".join(
    f"<!-- slop:{name} -->\nOLD-PLACEHOLDER\n<!-- /slop:{name} -->" for name in report.SECTIONS
)


@pytest.fixture
def bench_env(fakes, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    originals = tmp_path / "examples" / "originals"
    originals.mkdir(parents=True)
    Image.new("RGB", (300, 200), (200, 40, 40)).save(originals / "01_red.png")
    Image.new("RGB", (200, 300), (40, 40, 200)).save(originals / "04_painting.png")
    gen2 = FakeImageGen()
    gen2.name, gen2.model = "fakegen2", "fake-image-2"
    judge = FakeJudge()
    monkeypatch.setitem(providers.IMAGEGEN, "fakegen2", lambda **kw: gen2)
    monkeypatch.setitem(providers._KEY_CHECKS, "fakegen2", lambda: True)
    monkeypatch.setitem(providers.VISION, "fakejudge", lambda **kw: judge)
    monkeypatch.setitem(providers._KEY_CHECKS, "fakejudge", lambda: True)
    (tmp_path / "README.md").write_text(README_TEMPLATE)
    return tmp_path, originals


def test_metrics_basics():
    a = Image.new("RGB", (64, 64), (120, 120, 120))
    noisy = Image.effect_noise((64, 64), 60).convert("RGB")
    assert metrics.ssim(a, a) == pytest.approx(1.0)
    assert metrics.ssim(a, noisy) < 0.5
    assert metrics.parse_vibe("SCORE: 7 | Same night.") == (7.0, "Same night.")
    assert metrics.parse_vibe("score: 12|wow")[0] == 10.0
    assert metrics.parse_vibe("I refuse")[0] is None
    bw = metrics.effective_bandwidth(1000, 10, 0.5)
    assert bw.bytes_per_second == 100
    assert bw.bytes_per_dollar == 2000


def test_plan_skips_backends_without_keys(bench_env):
    _, originals = bench_env
    plan = bench_run.Plan.build(
        originals=originals,
        backends=["fakegen", "openai"],
        languages=["english"],
        vision="fake",
        judge="none",
    )
    assert plan.backends == ["fakegen"]
    assert "openai" in plan.skipped
    assert plan.counts()["encodes"] == 2


def test_plan_without_any_decoder(bench_env):
    _, originals = bench_env
    with pytest.raises(providers.MissingKeyError, match="OPENAI_API_KEY or GEMINI_API_KEY"):
        bench_run.Plan.build(
            originals=originals,
            backends=["openai", "gemini"],
            languages=["english"],
            vision="fake",
            judge="none",
        )


def test_full_bench_and_report(bench_env):
    root, originals = bench_env
    plan = bench_run.Plan.build(
        originals=originals,
        backends=["fakegen", "fakegen2", "gemini"],
        languages=["english", "terse"],
        vision="fake",
        judge="fakejudge",
        sweep="painting",
        sweep_budgets=["none", "kilobyte", "280", "160"],
        telephone=["01_red"],
        telephone_rounds=3,
    )
    assert plan.counts()["decode:fakegen"] == 4 + 3 + 3
    assert "estimated cost" in plan.describe()
    results = bench_run.execute(
        plan,
        results_dir=root / "benchmarks" / "results",
        outputs_dir=root / "examples" / "outputs",
    )
    runs = results["runs"]
    assert len([r for r in runs.values() if "matrix" in r.get("kinds", [])]) == 4
    red = runs["01_red|english|full"]
    assert set(red["decodes"]) == {"fakegen", "fakegen2"}
    assert red["decodes"]["fakegen"]["vibe_score"] == 7
    assert red["ratio"] > 0
    assert runs["04_painting|english|160B"]["packet_bytes"] <= 160
    assert len(results["telephone"]["01_red|fakegen"]["rounds"]) == 3
    assert results["meta"]["skipped_backends"] == {"gemini": "no API key in the environment"}

    saved = json.loads((root / "benchmarks/results/results.json").read_text())
    assert saved["runs"].keys() == runs.keys()

    readme = (root / "README.md").read_text()
    assert "OLD-PLACEHOLDER" not in readme
    assert "Vibe" in readme
    assert "56k modem" in readme
    assert "examples/outputs/grids/01_red.png" in readme
    assert (root / "examples/outputs/hero.png").exists()
    assert (root / "examples/outputs/grids/sweep.png").exists()
    assert (root / "examples/outputs/telephone/01_red/filmstrip.png").exists()
    assert (root / "benchmarks/results/summary.md").exists()


def test_bench_resumes_without_paying_twice(bench_env):
    root, originals = bench_env
    kwargs = dict(
        originals=originals,
        backends=["fakegen"],
        languages=["english"],
        vision="fake",
        judge="none",
    )
    bench_run.execute(
        bench_run.Plan.build(**kwargs), results_dir=root / "r", outputs_dir=root / "o"
    )
    vision = providers.VISION["fake"]()
    calls = len(vision.calls)
    bench_run.execute(
        bench_run.Plan.build(**kwargs), results_dir=root / "r", outputs_dir=root / "o"
    )
    assert len(vision.calls) == calls


def test_report_with_no_results(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(README_TEMPLATE)
    report.build(
        tmp_path / "missing.json", outputs_dir=tmp_path / "o", originals=tmp_path, readme=readme
    )
    assert "No benchmark results committed yet" in readme.read_text()


def test_cli_bench_dry_run(bench_env):
    _, originals = bench_env
    result = CliRunner().invoke(
        app,
        [
            "bench",
            str(originals),
            "--backends",
            "fakegen",
            "--vision",
            "fake",
            "--judge",
            "none",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "estimated cost" in result.output


def test_cli_bench_aborts_without_confirmation(bench_env):
    _, originals = bench_env
    result = CliRunner().invoke(
        app,
        ["bench", str(originals), "--backends", "fakegen", "--vision", "fake", "--judge", "none"],
        input="n\n",
    )
    assert result.exit_code != 0
    assert not (originals.parent.parent / "benchmarks" / "results" / "results.json").exists()
