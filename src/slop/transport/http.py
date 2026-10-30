"""SLOP over HTTP: ``POST /`` with a packet (binary or ``slop:`` text) as the body.

The server stores every packet it receives and, unless told otherwise, decodes
it into an image before replying with a JSON receipt.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from slop.packet import MAX_BODY, OVERHEAD, Packet, PacketError
from slop.transport import receive

CONTENT_TYPE = "application/vnd.slop"
#: Generous enough for the base64 text form of the largest legal packet.
MAX_REQUEST = (MAX_BODY + OVERHEAD) * 2

BANNER = """\
SLOP/1 receiver ready.
POST a SLOP packet (application/vnd.slop) or its slop: text form to this URL.
Pixels are not accepted here. We have moved beyond pixels.
"""


def make_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    out_dir: str | Path = "inbox",
    *,
    decode: bool = True,
    backend: str = "auto",
    seed: int | None = None,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SLOP/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            if not quiet:
                sys.stderr.write("slop serve: " + (format % args) + "\n")

        def _reply(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._reply(status, json.dumps(payload, indent=2).encode(), "application/json")

        def do_GET(self) -> None:
            self._reply(200, BANNER.encode(), "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._json(411, {"error": "Content-Length required"})
                return
            if length > MAX_REQUEST:
                self._json(413, {"error": f"{length} bytes is too many bytes for SLOP"})
                return
            body = self.rfile.read(length)
            try:
                packet = Packet.load(body)
            except (PacketError, UnicodeDecodeError) as exc:
                self._json(400, {"error": f"not a valid SLOP packet: {exc}"})
                return
            record = receive(packet, out_dir, decode=decode, backend=backend, seed=seed)
            self._json(200, {"status": "received", **record})

    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str, port: int, out_dir: str | Path, **kwargs: Any) -> None:
    server = make_server(host, port, out_dir, **kwargs)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def send(packet: Packet, url: str, *, text: bool = False, timeout: float = 600) -> dict[str, Any]:
    """POST a packet and return the receiver's JSON receipt."""
    body = packet.to_text().encode() if text else packet.to_bytes()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "text/plain" if text else CONTENT_TYPE},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ConnectionError(f"receiver said {exc.code}: {detail}") from exc
