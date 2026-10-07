"""Private transport over the Nym mixnet.

Each participant runs its own ``nym-client`` (websocket API, default
ws://127.0.0.1:1977). Message format follows nym's
``clients/native/websocket-requests/src/text.rs``:

  requests : {"type":"selfAddress"}
             {"type":"sendAnonymous","recipient":..,"message":..,"replySurbs":n}
             {"type":"reply","senderTag":..,"message":..}
  responses: {"type":"selfAddress","address":..}
             {"type":"received","message":..,"senderTag":..|null}
             {"type":"error","message":..}

Design: the relay is a mixnet *service*. Agents reach it with anonymous
sends carrying reply SURBs, so the relay learns neither their IP nor their
Nym address; it answers through the SURBs. The PactMesh protocol (outer
envelopes, routes, adverts, blobs) is unchanged.

What this does NOT give: protection if both ends are observed by a global
adversary, unlinkability of a stable mailbox route across polls, or
privacy of on-chain settlement. Unavailability is explicit: every
operation raises ``TransportUnavailable``; there is no fallback to direct.

Status: tested against a nym-client test double that implements the
message format above (tests/test_mixnet.py). Not yet run against a live
Nym network from the build environment (no access to it).
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time

from ..crypto import random_id
from ..httpbase import HttpError
from .ws import WebSocketClient, WsError

RPC_VERSION = "pactmesh-relay-rpc/1"
log = logging.getLogger("pactmesh.nym")


class TransportUnavailable(RuntimeError):
    pass


class NymClient:
    """Thin wrapper over a local nym-client websocket."""

    def __init__(self, url: str, timeout: float = 10.0):
        try:
            self.ws = WebSocketClient(url, timeout=timeout)
        except (OSError, WsError, ValueError) as e:
            raise TransportUnavailable(f"nym-client not reachable at {url}: {e}") from None
        self.received: queue.Queue = queue.Queue()
        self._addr: queue.Queue = queue.Queue()
        self.errors: list[str] = []
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        try:
            while True:
                msg = json.loads(self.ws.recv_text())
                t = msg.get("type")
                if t == "received":
                    self.received.put(msg)
                elif t == "selfAddress":
                    self._addr.put(msg["address"])
                elif t == "error":
                    self.errors.append(str(msg.get("message"))[:300])
                    log.warning("nym-client error: %s", msg.get("message"))
        except (WsError, OSError, ValueError):
            self.alive = False
            self.received.put(None)  # wake waiters

    def _send(self, obj: dict) -> None:
        if not self.alive:
            raise TransportUnavailable("nym-client connection lost")
        try:
            self.ws.send_text(json.dumps(obj))
        except OSError as e:
            self.alive = False
            raise TransportUnavailable(f"nym-client send failed: {e}") from None

    def self_address(self, timeout: float = 10.0) -> str:
        self._send({"type": "selfAddress"})
        try:
            return self._addr.get(timeout=timeout)
        except queue.Empty:
            raise TransportUnavailable("nym-client did not report its address") from None

    def send_anonymous(self, recipient: str, message: str, reply_surbs: int) -> None:
        self._send({"type": "sendAnonymous", "recipient": recipient, "message": message, "replySurbs": reply_surbs})

    def reply(self, sender_tag: str, message: str) -> None:
        self._send({"type": "reply", "senderTag": sender_tag, "message": message})

    def close(self) -> None:
        self.alive = False
        self.ws.close()


class MixnetTransport:
    mode = "mixnet"
    guarantees = ("encrypted; relay sees neither client IP nor Nym address (anonymous sends + reply SURBs); "
                  "timing protection depends on the Nym deployment")

    def __init__(self, nym_client_url: str | None, relay_address: str | None, timeout: float = 45.0,
                 reply_surbs: int = 10):
        if not nym_client_url or not relay_address:
            raise TransportUnavailable("mixnet mode needs --nym-client and --relay-nym (relay's Nym address)")
        self.url, self.relay, self.timeout, self.surbs = nym_client_url, relay_address, timeout, reply_surbs
        self.client: NymClient | None = None
        self.lock = threading.Lock()
        self.responses: dict[str, dict] = {}
        self.pending_acks: dict[str, list[str]] = {}

    def _connect(self) -> NymClient:
        if self.client is None or not self.client.alive:
            self.client = NymClient(self.url)
        return self.client

    def _rpc(self, op: str, **params) -> dict:
        with self.lock:
            c = self._connect()
            rid = random_id()
            c.send_anonymous(self.relay, json.dumps({"v": RPC_VERSION, "rid": rid, "op": op, **params}), self.surbs)
            deadline = time.time() + self.timeout
            while rid not in self.responses:
                left = deadline - time.time()
                if left <= 0:
                    raise TransportUnavailable(f"mixnet request {op} timed out after {self.timeout}s")
                try:
                    msg = c.received.get(timeout=left)
                except queue.Empty:
                    continue
                if msg is None:
                    raise TransportUnavailable("nym-client connection lost")
                try:
                    body = json.loads(msg["message"])
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(body, dict) and body.get("v") == RPC_VERSION and "rid" in body:
                    self.responses[body["rid"]] = body
            resp = self.responses.pop(rid)
        if not resp.get("ok"):
            raise HttpError(int(resp.get("status", 400)), str(resp.get("error", "RELAY_ERROR")))
        return resp["result"]

    # Same surface as DirectTransport -------------------------------------

    def send(self, envelope: dict) -> None:
        try:
            self._rpc("post", route=envelope["route"], env=envelope)
        except HttpError as e:
            raise TransportUnavailable(f"relay refused envelope: {e.code}") from None

    def receive(self, secret: str) -> list[tuple[str, dict]]:
        acks = self.pending_acks.pop(secret, [])
        try:
            out = self._rpc("fetch", secret=secret, ack=acks)  # acks piggyback to save a mixnet round trip
        except HttpError:
            return []
        return [("nym", env) for env in out["envelopes"]]

    def acknowledge(self, relay: str, secret: str, ids: list[str]) -> None:
        if ids:
            self.pending_acks.setdefault(secret, []).extend(ids)

    def status(self) -> dict:
        ok = self.client is not None and self.client.alive
        return {"mode": self.mode, "guarantees": self.guarantees, "available": ok,
                "relay": self.relay[:24] + "…"}

    def publish_advert(self, advert: dict) -> int:
        try:
            self._rpc("put_advert", advert=advert)
            return 1
        except HttpError:
            return 0

    def fetch_adverts(self) -> list[dict]:
        return self._rpc("adverts")["adverts"]

    def put_blob(self, blob_id: str, data: bytes) -> int:
        try:
            self._rpc("put_blob", blob_id=blob_id, data=base64.b64encode(data).decode())
        except HttpError as e:
            raise TransportUnavailable(f"relay refused blob: {e.code}") from None
        return 1

    def get_blob(self, blob_id: str) -> bytes:
        try:
            return base64.b64decode(self._rpc("get_blob", blob_id=blob_id)["data"])
        except HttpError:
            raise TransportUnavailable("blob not available") from None


class RelayNymGateway:
    """Serves a Relay over its own nym-client. Only anonymous requests (with a
    senderTag for SURB replies) are answered; the gateway never learns who
    the client is."""

    def __init__(self, relay, nym_client_url: str):
        self.relay = relay
        self.client = NymClient(nym_client_url)
        self.address = self.client.self_address()

    def handle(self, body: dict) -> dict:
        r, op = self.relay, body.get("op")
        if op == "post":
            return r.post_envelope(body["route"], body["env"], via="nym")
        if op == "fetch":
            if body.get("ack"):
                r.ack(body["secret"], body["ack"])
            return r.fetch(body["secret"])
        if op == "put_advert":
            return r.put_advert(body["advert"])
        if op == "adverts":
            return r.list_adverts()
        if op == "put_blob":
            return r.put_blob(body["blob_id"], base64.b64decode(body["data"]), via="nym")
        if op == "get_blob":
            return {"data": base64.b64encode(r.get_blob(body["blob_id"])).decode()}
        raise HttpError(400, "UNKNOWN_OP")

    def serve_forever(self) -> None:
        """Serve until the local nym-client goes away (then log and return)."""
        while True:
            msg = self.client.received.get()
            if msg is None:
                log.error("relay gateway: nym-client connection lost; private mode is DOWN")
                return
            tag = msg.get("senderTag")
            if not tag:
                continue  # not anonymous: cannot (and should not) answer
            try:
                body = json.loads(msg["message"])
                if body.get("v") != RPC_VERSION:
                    continue
                resp = {"v": RPC_VERSION, "rid": body["rid"], "ok": True, "result": self.handle(body)}
            except HttpError as e:
                resp = {"v": RPC_VERSION, "rid": body.get("rid"), "ok": False, "error": e.code, "status": e.status}
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            try:
                self.client.reply(tag, json.dumps(resp))
            except TransportUnavailable:
                log.error("relay gateway: cannot reply; private mode is DOWN")
                return
