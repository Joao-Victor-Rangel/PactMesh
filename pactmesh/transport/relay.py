"""Store-and-forward relay.

The relay only ever sees opaque outer envelopes (version, random id,
mailbox route, coarse expiry, size class, ephemeral key, nonce,
ciphertext). It also mirrors signed service adverts and stores opaque
encrypted blobs. It never receives keys or plaintext.

It can be reached two ways, with different guarantees:
* HTTP (direct mode): encrypted, NOT anonymous - the relay sees client IPs.
* Nym mixnet (private mode, see ``nym.py``): clients send anonymously with
  reply SURBs, so the relay sees neither their IP nor their Nym address.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

import re

from ..crypto import MAX_CONTROL_MESSAGE, SIZE_CLASSES, TRANSPORT_VERSION, mailbox_route
from ..httpbase import App, HttpError, Raw, Request
from ..protocol import ProtocolError, validate_advert
from ..util import now

MAX_QUEUE = 1000
MAX_BLOB = 8 * 1024 * 1024
HEADER_KEYS = {"v", "id", "route", "exp", "size", "eph", "nonce", "ct"}
_SECRET = re.compile(r"^[0-9a-f]{32}$")


def _route_of(secret) -> str:
    if not isinstance(secret, str) or not _SECRET.match(secret):
        raise HttpError(400, "BAD_MAILBOX_SECRET")
    return mailbox_route(secret)


class Relay:
    def __init__(self, observation_log: Path | None = None):
        self.lock = threading.Lock()
        self.mailboxes: dict[str, dict[str, dict]] = defaultdict(dict)
        self.adverts: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.observation_log = observation_log
        self.app = App()
        self._routes()

    def _observe(self, record: dict) -> None:
        # What a curious relay operator can see. Used by privacy tests.
        if self.observation_log:
            with open(self.observation_log, "a") as f:
                f.write(json.dumps(record) + "\n")

    # ------------------------------------------------- operations (any channel)

    def post_envelope(self, route: str, env, via: str = "http") -> dict:
        if not isinstance(env, dict) or set(env) != HEADER_KEYS:
            raise HttpError(400, "BAD_ENVELOPE")
        if env["v"] != TRANSPORT_VERSION or env["route"] != route or env["size"] not in SIZE_CLASSES:
            raise HttpError(400, "BAD_ENVELOPE")
        if not isinstance(env["ct"], str) or len(env["ct"]) > (MAX_CONTROL_MESSAGE + 64) * 4 // 3 + 8:
            raise HttpError(413, "TOO_LARGE")
        if not isinstance(env["exp"], int) or env["exp"] < now():
            raise HttpError(400, "EXPIRED")
        with self.lock:
            box = self.mailboxes[route]
            if len(box) >= MAX_QUEUE:
                raise HttpError(429, "QUEUE_FULL")
            box[env["id"]] = env
        self._observe({"op": "post", "via": via, "route": route, "id": env["id"], "size": env["size"],
                       "exp": env["exp"], "ct_prefix": env["ct"][:16]})
        return {"accepted": env["id"]}

    def fetch(self, secret: str) -> dict:
        """Reading requires the mailbox secret; the public route is not enough."""
        route = _route_of(secret)
        t = now()
        with self.lock:
            box = self.mailboxes.get(route, {})
            for k in [k for k, e in box.items() if e["exp"] < t]:
                del box[k]
            return {"envelopes": list(box.values())[:100]}

    def ack(self, secret: str, ids) -> dict:
        route = _route_of(secret)
        ids = ids if isinstance(ids, list) else []
        with self.lock:
            box = self.mailboxes.get(route, {})
            for i in ids:
                box.pop(i, None)
        return {"acked": len(ids)}

    def put_advert(self, adv) -> dict:
        try:
            validate_advert(adv)
        except ProtocolError as e:
            raise HttpError(400, e.code, e.detail)
        with self.lock:
            self.adverts[adv["agent_key_id"]] = adv
        return {"ok": True}

    def list_adverts(self) -> dict:
        with self.lock:
            return {"adverts": list(self.adverts.values())}

    def put_blob(self, blob_id: str, data: bytes, via: str = "http") -> dict:
        if len(data) > MAX_BLOB:
            raise HttpError(413, "TOO_LARGE")
        with self.lock:
            self.blobs[blob_id] = data
        self._observe({"op": "blob", "via": via, "id": blob_id, "bytes": len(data), "prefix": data[:16].hex()})
        return {"ok": True}

    def get_blob(self, blob_id: str) -> bytes:
        with self.lock:
            if blob_id not in self.blobs:
                raise HttpError(404, "NOT_FOUND")
            return self.blobs[blob_id]

    # -------------------------------------------------------------- HTTP

    def _routes(self):
        app = self.app

        @app.route("GET", "/health")
        def health(req):
            return {"ok": True, "role": "relay", "mode": "direct (encrypted, not anonymous)"}

        @app.route("POST", "/mailbox/([0-9a-f]{32})")
        def post(req: Request, route: str):
            return self.post_envelope(route, req.json())

        @app.route("POST", "/mailbox/fetch")
        def fetch(req: Request):
            return self.fetch((req.json() or {}).get("secret"))

        @app.route("POST", "/mailbox/ack")
        def ack(req: Request):
            body = req.json() or {}
            return self.ack(body.get("secret"), body.get("ids"))

        @app.route("POST", "/adverts")
        def put_advert(req: Request):
            return self.put_advert(req.json())

        @app.route("GET", "/adverts")
        def list_adverts(req: Request):
            return self.list_adverts()

        @app.route("PUT", "/blobs/([0-9a-f]{32})")
        def put_blob(req: Request, blob_id: str):
            return self.put_blob(blob_id, req.raw)

        @app.route("GET", "/blobs/([0-9a-f]{32})")
        def get_blob(req: Request, blob_id: str):
            return Raw(self.get_blob(blob_id))
