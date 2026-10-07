"""Transport backends.

Both backends move opaque outer envelopes; the Cripito inner protocol is
identical on either. They have different names because they give
different guarantees:

* ``direct``  - encrypted end to end through store-and-forward relays.
                NOT anonymous: relays see mailbox, timing and IP.
* ``mixnet``  - private mode through a Nym client. Fails explicitly when
                unavailable; it never silently downgrades to direct.
"""

from __future__ import annotations

import random

from ..httpbase import HttpError, call


class TransportUnavailable(RuntimeError):
    pass


class DirectTransport:
    mode = "direct"
    guarantees = "end-to-end encrypted; NOT anonymous (relay sees mailbox, timing, IP)"

    def __init__(self, relays: list[str]):
        if not relays:
            raise ValueError("at least one relay is required")
        self.relays = relays

    def send(self, envelope: dict) -> None:
        last = None
        for relay in self.relays:
            try:
                call("POST", f"{relay}/mailbox/{envelope['route']}", envelope, timeout=5)
                return
            except (HttpError, OSError) as e:
                last = e
        raise TransportUnavailable(f"no relay accepted the envelope: {last}")

    def receive(self, route: str) -> list[tuple[str, dict]]:
        out = []
        for relay in self.relays:
            try:
                for env in call("GET", f"{relay}/mailbox/{route}", timeout=5)["envelopes"]:
                    out.append((relay, env))
            except (HttpError, OSError):
                continue
        return out

    def acknowledge(self, relay: str, route: str, ids: list[str]) -> None:
        if ids:
            call("POST", f"{relay}/mailbox/{route}/ack", ids, timeout=5)

    def status(self) -> dict:
        up = []
        for relay in self.relays:
            try:
                call("GET", f"{relay}/health", timeout=2)
                up.append(relay)
            except (HttpError, OSError):
                pass
        return {"mode": self.mode, "guarantees": self.guarantees, "relays_up": up}

    # Adverts and encrypted blobs are mirrored on every reachable relay.
    def publish_advert(self, advert: dict) -> int:
        ok = 0
        for relay in self.relays:
            try:
                call("POST", f"{relay}/adverts", advert, timeout=5)
                ok += 1
            except (HttpError, OSError):
                pass
        return ok

    def fetch_adverts(self) -> list[dict]:
        out = []
        for relay in self.relays:
            try:
                out.extend(call("GET", f"{relay}/adverts", timeout=5)["adverts"])
            except (HttpError, OSError):
                pass
        return out

    def put_blob(self, blob_id: str, data: bytes) -> int:
        ok = 0
        for relay in self.relays:
            try:
                call("PUT", f"{relay}/blobs/{blob_id}", data, timeout=10)
                ok += 1
            except (HttpError, OSError):
                pass
        if not ok:
            raise TransportUnavailable("no relay stored the blob")
        return ok

    def get_blob(self, blob_id: str) -> bytes:
        for relay in self.relays:
            try:
                return call("GET", f"{relay}/blobs/{blob_id}", timeout=10, raw=True)
            except (HttpError, OSError):
                continue
        raise TransportUnavailable("blob not available on any relay")


class MixnetTransport:
    """Private backend placeholder for a Nym client (websocket API).

    Not implemented in this prototype. Every operation raises
    ``TransportUnavailable`` so private mode fails loudly instead of
    falling back to direct.
    """

    mode = "mixnet"
    guarantees = "intended: sender/receiver unlinkability via Nym mixnet (NOT IMPLEMENTED)"

    def __init__(self, nym_client_url: str | None = None):
        self.nym_client_url = nym_client_url

    def _unavailable(self, *a, **k):
        raise TransportUnavailable(
            "private (mixnet) transport is not available in this build; refusing to downgrade to direct"
        )

    send = receive = acknowledge = publish_advert = fetch_adverts = put_blob = get_blob = _unavailable

    def status(self) -> dict:
        return {"mode": self.mode, "guarantees": self.guarantees, "available": False}


def backoff(attempt: int, base: float = 1.0, cap: float = 16.0) -> float:
    """Exponential backoff with full jitter."""
    return random.uniform(0, min(cap, base * (2**attempt)))


def make_transport(mode: str, relays: list[str], nym_client_url: str | None = None):
    if mode == "direct":
        return DirectTransport(relays)
    if mode == "mixnet":
        return MixnetTransport(nym_client_url)
    raise ValueError(f"unknown transport {mode!r}")


__all__ = ["DirectTransport", "MixnetTransport", "TransportUnavailable", "make_transport", "backoff"]
