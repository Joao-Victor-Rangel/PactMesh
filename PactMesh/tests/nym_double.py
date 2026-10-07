"""Test double for a network of nym-clients (websocket text API).

Implements the request/response shapes of nym's websocket-requests text.rs:
selfAddress, sendAnonymous (delivered with a random senderTag that only the
mixnet can map back), reply (delivered without a tag) and error. Each
participant gets its own listening socket, like running its own nym-client.
It records everything each endpoint *receives*, so tests can assert what a
relay operator could learn.
"""

import json
import secrets
import socket
import threading
import time

from pactmesh.transport.ws import OP_TEXT, WsError, recv_message, send_frame, server_handshake


class MockMixnet:
    def __init__(self, latency: float = 0.005):
        self.latency = latency
        self.clients: dict[str, "MockNymClient"] = {}
        self.tags: dict[str, "MockNymClient"] = {}
        self.lock = threading.Lock()

    def client(self) -> "MockNymClient":
        c = MockNymClient(self)
        with self.lock:
            self.clients[c.address] = c
        return c

    def route(self, sender: "MockNymClient", req: dict) -> None:
        time.sleep(self.latency)
        t = req.get("type")
        if t == "sendAnonymous":
            dest = self.clients.get(req["recipient"])
            if not dest:
                return sender.push({"type": "error", "message": "unknown recipient"})
            tag = secrets.token_hex(8)
            with self.lock:
                self.tags[tag] = sender
            dest.push({"type": "received", "message": req["message"], "senderTag": tag})
        elif t == "send":
            dest = self.clients.get(req["recipient"])
            if dest:
                dest.push({"type": "received", "message": req["message"], "senderTag": None})
        elif t == "reply":
            dest = self.tags.get(req["senderTag"])
            if dest:
                dest.push({"type": "received", "message": req["message"], "senderTag": None})
        elif t == "selfAddress":
            sender.push({"type": "selfAddress", "address": sender.address})
        else:
            sender.push({"type": "error", "message": f"malformed request {t}"})


class MockNymClient:
    def __init__(self, net: MockMixnet):
        self.net = net
        self.address = f"{secrets.token_hex(16)}.{secrets.token_hex(16)}@gw{secrets.token_hex(4)}"
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.url = f"ws://127.0.0.1:{self.srv.getsockname()[1]}"
        self.conn = None
        self.send_lock = threading.Lock()
        self.received_log: list[dict] = []  # what this endpoint's owner receives
        self.backlog: list[dict] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            if getattr(self, "stopped", False):
                conn.close()
                return
            server_handshake(conn)
            with self.send_lock:
                self.conn = conn
                for m in self.backlog:
                    send_frame(conn, OP_TEXT, json.dumps(m).encode(), mask=False)
                self.backlog.clear()
            try:
                while True:
                    _op, data = recv_message(conn, reply_mask=False)
                    self.net.route(self, json.loads(data))
            except (WsError, OSError, ValueError):
                with self.send_lock:
                    self.conn = None

    def push(self, msg: dict) -> None:
        self.received_log.append(msg)
        with self.send_lock:
            if self.conn is None:
                self.backlog.append(msg)
                return
            try:
                send_frame(self.conn, OP_TEXT, json.dumps(msg).encode(), mask=False)
            except OSError:
                self.backlog.append(msg)

    def stop(self):
        self.stopped = True
        for s in (self.srv, self.conn):
            if s is None:
                continue
            try:
                s.shutdown(socket.SHUT_RDWR)  # unblocks accept()/recv() in the serving thread
            except OSError:
                pass
            s.close()
