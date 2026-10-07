"""Minimal RFC 6455 WebSocket (text frames) on the standard library.

Enough to talk to a local ``nym-client`` (ws://127.0.0.1:1977) and to run a
test double of it. Not a general-purpose WebSocket implementation.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
from urllib.parse import urlparse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 8, 9, 10
MAX_FRAME = 64 * 1024 * 1024


class WsError(ConnectionError):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise WsError("connection closed")
        buf += chunk
    return bytes(buf)


def send_frame(sock: socket.socket, opcode: int, payload: bytes, mask: bool) -> None:
    head = bytearray([0x80 | opcode])
    n = len(payload)
    mbit = 0x80 if mask else 0
    if n < 126:
        head.append(mbit | n)
    elif n < 1 << 16:
        head.append(mbit | 126)
        head += struct.pack(">H", n)
    else:
        head.append(mbit | 127)
        head += struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        head += key
    sock.sendall(bytes(head) + payload)


def recv_message(sock: socket.socket, reply_mask: bool) -> tuple[int, bytes]:
    """Return (opcode, payload) of the next complete data message; answers pings."""
    parts, first_op = [], None
    while True:
        b0, b1 = _read_exact(sock, 2)
        fin, op = b0 & 0x80, b0 & 0x0F
        n = b1 & 0x7F
        if n == 126:
            (n,) = struct.unpack(">H", _read_exact(sock, 2))
        elif n == 127:
            (n,) = struct.unpack(">Q", _read_exact(sock, 8))
        if n > MAX_FRAME:
            raise WsError("frame too large")
        key = _read_exact(sock, 4) if b1 & 0x80 else None
        data = _read_exact(sock, n)
        if key:
            data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
        if op == OP_PING:
            send_frame(sock, OP_PONG, data, reply_mask)
            continue
        if op == OP_PONG:
            continue
        if op == OP_CLOSE:
            raise WsError("closed by peer")
        if op != OP_CONT:
            first_op = op
        parts.append(data)
        if fin:
            return first_op or OP_TEXT, b"".join(parts)


class WebSocketClient:
    def __init__(self, url: str, timeout: float = 10.0):
        u = urlparse(url)
        if u.scheme != "ws":
            raise ValueError("only ws:// (local nym-client) is supported")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise WsError("handshake failed")
            head += chunk
        status, *lines = head.split(b"\r\n\r\n")[0].decode(errors="replace").split("\r\n")
        headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines if ":" in ln)}
        if " 101 " not in status + " " or headers.get("sec-websocket-accept") != accept_key(key):
            raise WsError(f"bad handshake: {status}")
        self.sock.settimeout(None)

    def send_text(self, text: str) -> None:
        send_frame(self.sock, OP_TEXT, text.encode(), mask=True)

    def recv_text(self) -> str:
        _op, data = recv_message(self.sock, reply_mask=True)
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            send_frame(self.sock, OP_CLOSE, b"", mask=True)
        except OSError:
            pass
        self.sock.close()


def server_handshake(conn: socket.socket) -> None:
    """Server side of the handshake (used by the nym-client test double)."""
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = conn.recv(1024)
        if not chunk:
            raise WsError("handshake aborted")
        head += chunk
    lines = head.split(b"\r\n\r\n")[0].decode().split("\r\n")[1:]
    headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines if ":" in ln)}
    key = headers["sec-websocket-key"]
    conn.sendall((f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode())
