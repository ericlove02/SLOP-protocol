"""SLOP over a raw TCP socket, for maximum retro.

A SLOP packet is self-delimiting (the length is in the header), so the wire
protocol is: client writes one packet, server replies with one line,
``ACK <bytes> <crc32-hex>`` or ``NAK <reason>``, and closes. Decoding happens
after the ACK; the sender has better things to do than wait for a GPU.
"""

from __future__ import annotations

import contextlib
import socket
import socketserver
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from slop.packet import HEADER_SIZE, Packet, PacketError, read_header
from slop.transport import receive


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining:
        chunk = sock.recv(min(remaining, 65536))
        if not chunk:
            raise ConnectionError(f"connection closed with {remaining} bytes outstanding")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_packet(sock: socket.socket) -> Packet:
    header = _recv_exactly(sock, HEADER_SIZE)
    _body_len, total = read_header(header)
    rest = _recv_exactly(sock, total - HEADER_SIZE)
    return Packet.from_bytes(header + rest)


def make_server(
    host: str = "0.0.0.0",
    port: int = 5150,
    out_dir: str | Path = "inbox",
    *,
    decode: bool = True,
    backend: str = "auto",
    seed: int | None = None,
    on_receive: Callable[[dict[str, Any]], None] | None = None,
    quiet: bool = False,
) -> socketserver.ThreadingTCPServer:
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock: socket.socket = self.request
            sock.settimeout(30)
            try:
                packet = recv_packet(sock)
            except (PacketError, ConnectionError, TimeoutError) as exc:
                reason = str(exc).replace("\n", " ")
                with contextlib.suppress(OSError):
                    sock.sendall(f"NAK {reason}\n".encode())
                return
            raw = packet.to_bytes()
            sock.sendall(f"ACK {len(raw)} {raw[-4:].hex()}\n".encode())
            sock.close()
            record = receive(packet, out_dir, decode=decode, backend=backend, seed=seed)
            if not quiet:
                where = record.get("image") or record.get("decode_error") or record["packet"]
                sys.stderr.write(
                    f"slop listen: {record['bytes']} bytes "
                    f"from {self.client_address[0]} -> {where}\n"
                )
            if on_receive:
                on_receive(record)

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def send(packet: Packet, host: str, port: int, *, timeout: float = 30) -> str:
    """Send one packet; return the receiver's reply line."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(packet.to_bytes())
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = sock.recv(256)
            if not chunk:
                break
            reply += chunk
    line = reply.decode(errors="replace").strip()
    if not line.startswith("ACK"):
        raise ConnectionError(f"receiver did not ACK: {line or '(silence)'}")
    return line


def parse_address(address: str, default_host: str = "127.0.0.1") -> tuple[str, int]:
    """'host:port' or ':port' or 'port' -> (host, port)."""
    host, _, port = address.rpartition(":")
    return (host or default_host), int(port)
