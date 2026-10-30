import json

from PIL import Image
from typer.testing import CliRunner

from conftest import LONG
from slop.cli import app
from slop.packet import Packet

runner = CliRunner()


def test_help_and_version():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Semantically Lossy" in result.output
    assert runner.invoke(app, ["--version"]).output.startswith("slop ")


def test_encode_inspect_decode(fakes, sample_image, tmp_path):
    packet_path = tmp_path / "photo.slop"
    result = runner.invoke(
        app, ["encode", str(sample_image), "-o", str(packet_path), "--vision", "fake", "--text"]
    )
    assert result.exit_code == 0, result.output
    assert "slop:" in result.stdout
    assert Packet.load(packet_path).description == LONG

    result = runner.invoke(app, ["inspect", str(packet_path)])
    assert result.exit_code == 0, result.output
    assert "cypress" in result.stdout
    assert "crc32" in result.stdout

    result = runner.invoke(app, ["inspect", str(packet_path), "--json"])
    info = json.loads(result.stdout)
    assert info["description"] == LONG
    assert info["sizes"]["none"] >= info["sizes"]["zlib"] - 20

    out = tmp_path / "out.png"
    result = runner.invoke(
        app, ["decode", str(packet_path), "-o", str(out), "--backend", "fakegen", "--seed", "3"]
    )
    assert result.exit_code == 0, result.output
    assert Image.open(out).size[0] > 0


def test_encode_budget_named(fakes, sample_image, tmp_path):
    packet_path = tmp_path / "tiny.slop"
    result = runner.invoke(
        app,
        [
            "encode",
            str(sample_image),
            "-o",
            str(packet_path),
            "--vision",
            "fake",
            "--max-bytes",
            "sms",
            "--terse",
        ],
    )
    assert result.exit_code == 0, result.output
    packet = Packet.load(packet_path)
    assert len(packet.to_bytes()) <= 160
    assert packet.terse


def test_bad_budget_name(fakes, sample_image):
    result = runner.invoke(app, ["encode", str(sample_image), "--max-bytes", "carrier-pigeon"])
    assert result.exit_code != 0


def test_decode_without_keys_explains(sample_image, tmp_path):
    packet_path = Packet("a red square").save(tmp_path / "p.slop")
    result = runner.invoke(app, ["decode", str(packet_path)])
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def test_inspect_rejects_non_slop(tmp_path):
    bogus = tmp_path / "bogus.slop"
    bogus.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    result = runner.invoke(app, ["inspect", str(bogus)])
    assert result.exit_code == 1


def test_qr_round_trip(fakes, sample_image, tmp_path):
    qr_path = tmp_path / "qr.png"
    result = runner.invoke(app, ["qr", str(sample_image), "-o", str(qr_path), "--vision", "fake"])
    assert result.exit_code == 0, result.output
    packet_path = tmp_path / "from_qr.slop"
    out = tmp_path / "from_qr.png"
    result = runner.invoke(
        app,
        [
            "unqr",
            str(qr_path),
            "-o",
            str(out),
            "--packet",
            str(packet_path),
            "--backend",
            "fakegen",
        ],
    )
    assert result.exit_code == 0, result.output
    assert Packet.load(packet_path).description == LONG
    assert out.exists()


def test_telephone(fakes, sample_image, tmp_path):
    out_dir = tmp_path / "tel"
    result = runner.invoke(
        app,
        [
            "telephone",
            str(sample_image),
            "--rounds",
            "3",
            "--out",
            str(out_dir),
            "--vision",
            "fake",
            "--backend",
            "fakegen",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    for name in ("gen00_original.png", "gen01.png", "gen03.png", "gen03.slop", "filmstrip.png"):
        assert (out_dir / name).exists(), name


def test_doctor_lists_providers():
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "anthropic" in result.stdout
    assert "no key" in result.stdout


def test_send_requires_one_destination(sample_image):
    result = runner.invoke(app, ["send", str(sample_image)])
    assert result.exit_code != 0
