import json
import socket
import threading
import time
import urllib.request

import pytest
from PIL import Image
from typer.testing import CliRunner

from conftest import LONG
from slop.cli import app
from slop.packet import Packet
from slop.transport import http, tcp
from slop.transport.qr import packet_to_qr, qr_to_packet


@pytest.fixture
def http_server(fakes, tmp_path):
    server = http.make_server("127.0.0.1", 0, tmp_path / "inbox", backend="fakegen", quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}/", tmp_path / "inbox"
    server.shutdown()
    server.server_close()


def test_http_round_trip(http_server):
    url, inbox = http_server
    receipt = http.send(Packet(LONG, width=640, height=480), url)
    assert receipt["status"] == "received"
    assert receipt["backend"] == "fakegen"
    assert Image.open(receipt["image"]).size[0] > 0
    assert Packet.load(receipt["packet"]).description == LONG
    assert (inbox / f"{receipt['id']}.txt").read_text().strip() == LONG


def test_http_text_form(http_server):
    url, _ = http_server
    receipt = http.send(Packet("a lighthouse at dusk"), url, text=True)
    assert receipt["bytes"] > 0


def test_http_rejects_garbage(http_server):
    url, _ = http_server
    request = urllib.request.Request(url, data=b"definitely a jpeg", method="POST")
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request)
    assert info.value.code == 400
    assert "not a valid SLOP packet" in json.loads(info.value.read())["error"]


def test_http_get_banner(http_server):
    url, _ = http_server
    with urllib.request.urlopen(url) as response:
        assert b"moved beyond pixels" in response.read()


def test_cli_send_http(http_server, sample_image):
    url, inbox = http_server
    result = CliRunner().invoke(app, ["send", str(sample_image), "--to", url, "--vision", "fake"])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)
    assert receipt["image"].endswith(".png")
    assert list(inbox.glob("*.png"))


def test_http_decode_failure_is_reported(tmp_path):
    # no keys, backend=auto: server still stores the packet and explains
    server = http.make_server("127.0.0.1", 0, tmp_path, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        receipt = http.send(Packet("a cat"), f"http://{host}:{port}/")
        assert "decode_error" in receipt
        assert "OPENAI_API_KEY" in receipt["decode_error"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def tcp_server(fakes, tmp_path):
    received = []
    done = threading.Event()

    def on_receive(record):
        received.append(record)
        done.set()

    server = tcp.make_server(
        "127.0.0.1", 0, tmp_path / "inbox", backend="fakegen", on_receive=on_receive, quiet=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], received, done
    server.shutdown()
    server.server_close()


def test_tcp_round_trip(tcp_server):
    port, received, done = tcp_server
    packet = Packet(LONG)
    reply = tcp.send(packet, "127.0.0.1", port)
    assert reply.startswith(f"ACK {len(packet.to_bytes())} ")
    assert done.wait(5)
    assert received[0]["image"].endswith(".png")


def test_tcp_nak_on_garbage(tcp_server):
    port, _, _ = tcp_server
    with socket.create_connection(("127.0.0.1", port)) as sock:
        sock.sendall(b"GIF89a" + b"\0" * 20)
        reply = sock.recv(256)
    assert reply.startswith(b"NAK")


def test_cli_send_tcp(tcp_server, tmp_path):
    port, _received, done = tcp_server
    packet_path = Packet("a teapot").save(tmp_path / "t.slop")
    result = CliRunner().invoke(app, ["send", str(packet_path), "--tcp", f"127.0.0.1:{port}"])
    assert result.exit_code == 0, result.output
    assert "ACK" in result.output
    assert done.wait(5)


def test_parse_address():
    assert tcp.parse_address("example.com:9") == ("example.com", 9)
    assert tcp.parse_address("5150") == ("127.0.0.1", 5150)
    assert tcp.parse_address(":5150", default_host="0.0.0.0") == ("0.0.0.0", 5150)


def test_qr_round_trip():
    packet = Packet(LONG, width=800, height=600, terse=True)
    image = packet_to_qr(packet)
    assert qr_to_packet(image) == packet
    # survives a little abuse
    blurry = image.resize((image.width // 2, image.height // 2)).resize(image.size)
    assert qr_to_packet(blurry) == packet


def test_qr_too_big():
    import os

    from slop.packet import Compression, PacketError

    huge = Packet(os.urandom(3000).hex(), compression=Compression.NONE)
    with pytest.raises(PacketError, match="QR code holds"):
        packet_to_qr(huge)


def test_qr_rejects_plain_image():
    from slop.packet import PacketError

    with pytest.raises(PacketError, match="no QR code"):
        qr_to_packet(Image.new("RGB", (200, 200), "white"))


# keep slow sockets from hanging the suite forever
@pytest.fixture(autouse=True)
def _socket_timeout():
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(10)
    yield
    socket.setdefaulttimeout(old)
    time.sleep(0)
