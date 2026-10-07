"""PactMesh protocol pactmesh/0.1: inner message format, closed type
enumeration and per-type payload schemas.

Every inner message is canonical JSON signed with Ed25519 over the object
without its ``signature`` field. Unknown versions, unknown types, unknown
fields and out-of-window timestamps are rejected before any state change.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .canonical import canonical, hash_obj
from .crypto import random_id, verify_obj
from .util import iso, now, parse_iso

PROTOCOL_VERSION = "pactmesh/0.1"
CLOCK_SKEW = 60  # seconds tolerated for created_at in the future

MESSAGE_TYPES = (
    "SERVICE_ADVERT",
    "TASK_REQUEST",
    "QUOTE",
    "COUNTEROFFER",
    "ACCEPT",
    "DECLINE",
    "FUNDING_NOTICE",
    "DELIVERY",
    "RECEIPT",
    "CANCEL",
    "ACK",
    "ERROR",
)


class ProtocolError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------- tiny schemas

Validator = Callable[[Any, str], None]
_HEX = re.compile(r"^[0-9a-f]+$")
_INTSTR = re.compile(r"^(0|[1-9][0-9]{0,30})$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _fail(path: str, why: str) -> None:
    raise ProtocolError("SCHEMA_INVALID", f"{path}: {why}")


def s(maxlen: int = 256) -> Validator:
    def v(x, p):
        if not isinstance(x, str) or len(x) > maxlen:
            _fail(p, f"expected string <= {maxlen}")

    return v


def hexs(nbytes: int) -> Validator:
    def v(x, p):
        if not isinstance(x, str) or len(x) != nbytes * 2 or not _HEX.match(x):
            _fail(p, f"expected {nbytes}-byte lowercase hex")

    return v


def intstr(x, p):
    if not isinstance(x, str) or not _INTSTR.match(x):
        _fail(p, "expected non-negative integer string")


def ts(x, p):
    if not isinstance(x, str) or not _ISO.match(x):
        _fail(p, "expected UTC timestamp YYYY-MM-DDTHH:MM:SSZ")


def i(lo: int = 0, hi: int = 2**31) -> Validator:
    def v(x, p):
        if not isinstance(x, int) or isinstance(x, bool) or not lo <= x <= hi:
            _fail(p, f"expected integer in [{lo}, {hi}]")

    return v


def b(x, p):
    if not isinstance(x, bool):
        _fail(p, "expected boolean")


def enum(*vals) -> Validator:
    def v(x, p):
        if x not in vals:
            _fail(p, f"expected one of {vals}")

    return v


def lst(item: Validator, maxlen: int = 32) -> Validator:
    def v(x, p):
        if not isinstance(x, list) or len(x) > maxlen:
            _fail(p, f"expected list <= {maxlen}")
        for n, it in enumerate(x):
            item(it, f"{p}[{n}]")

    return v


def anyobj(x, p):
    if not isinstance(x, dict):
        _fail(p, "expected object")
    canonical(x)  # rejects floats and other non-canonical content


def opt(inner: Validator) -> Validator:
    def v(x, p):
        if x is not None:
            inner(x, p)

    v.optional = True  # type: ignore[attr-defined]
    return v


def obj(schema: dict[str, Validator]) -> Validator:
    def v(x, p):
        if not isinstance(x, dict):
            _fail(p, "expected object")
        extra = set(x) - set(schema)
        if extra:
            _fail(p, f"unknown fields {sorted(extra)}")
        for k, fv in schema.items():
            if k not in x:
                if getattr(fv, "optional", False):
                    continue
                _fail(f"{p}.{k}", "missing")
            fv(x[k], f"{p}.{k}")

    return v


VERIFIER = obj({"name": s(64), "version": s(16)})
KEY = hexs(32)
ID = hexs(16)
NETWORK = s(64)
ASSET = s(64)

TERMS = obj(
    {
        "task_id": ID,
        "service": s(64),
        "verifier": VERIFIER,
        "price": intstr,
        "asset": ASSET,
        "network": NETWORK,
        "delivery_seconds": i(1, 86400),
        "valid_until": ts,
        "payee": s(128),
        "supplier_key_id": KEY,
        "dataset_sha256": hexs(32),
    }
)

AGREEMENT = obj(
    {
        "protocol_version": enum(PROTOCOL_VERSION),
        "task_id": ID,
        "quote_id": ID,
        "terms_hash": hexs(32),
        "buyer_key_id": KEY,
        "supplier_key_id": KEY,
        "payer": s(128),
        "payee": s(128),
        "price": intstr,
        "asset": ASSET,
        "network": NETWORK,
        "verifier": VERIFIER,
        "dataset_sha256": hexs(32),
        "delivery_deadline": ts,
        "escrow_deadline": ts,
    }
)

PAYLOADS: dict[str, Validator] = {
    "TASK_REQUEST": obj(
        {
            "task_id": ID,
            "service": s(64),
            "verifier": VERIFIER,
            "requirements": obj(
                {
                    "format": enum("json"),
                    "column": s(64),
                    "percentiles": lst(i(1, 99), 9),
                    "max_delivery_seconds": i(1, 86400),
                }
            ),
            "dataset": obj({"sha256": hexs(32), "rows": i(1, 10**7), "bytes": i(1, 10**9)}),
            "asset": ASSET,
            "network": NETWORK,
            "reply_route": ID,
            "reply_enc_key": s(64),
            "quote_deadline": ts,
        }
    ),
    "QUOTE": obj(
        {
            "quote_id": ID,
            "round": i(0, 16),
            "in_response_to": opt(ID),
            "terms": TERMS,
            "terms_hash": hexs(32),
            # Untrusted third-party text. It is data, never an instruction.
            "description": s(500),
        }
    ),
    "COUNTEROFFER": obj({"quote_id": ID, "terms_hash": hexs(32), "price": intstr, "round": i(1, 16)}),
    "ACCEPT": obj(
        {
            "quote_id": ID,
            "terms_hash": hexs(32),
            "agreement": AGREEMENT,
            "agreement_hash": hexs(32),
            "buyer_signature": hexs(64),
            "supplier_signature": opt(hexs(64)),
        }
    ),
    "DECLINE": obj({"ref": ID, "reason_code": s(64)}),
    "FUNDING_NOTICE": obj(
        {
            "agreement_hash": hexs(32),
            "escrow_id": s(128),
            "tx_ref": s(128),
            "amount": intstr,
            "asset": ASSET,
            "network": NETWORK,
            "dataset_access": obj(
                {
                    "blob_id": ID,
                    "key": s(64),
                    "header": s(64),
                    "chunk": i(1, 1 << 20),
                    "sha256": hexs(32),
                    "purpose": s(64),
                }
            ),
        }
    ),
    "DELIVERY": obj({"agreement_hash": hexs(32), "report": anyobj, "report_sha256": hexs(32)}),
    "RECEIPT": obj(
        {
            "agreement_hash": hexs(32),
            "status": enum("ACCEPTED", "REJECTED"),
            "report_sha256": opt(hexs(32)),
            "verification": obj({"verifier": VERIFIER, "ok": b, "errors": lst(s(200), 32)}),
            "settlement": obj({"escrow_id": s(128), "tx_ref": opt(s(128)), "state": s(32)}),
        }
    ),
    "CANCEL": obj({"ref": ID, "reason_code": s(64)}),
    "ACK": obj({"ack_message_id": ID}),
    "ERROR": obj({"ref_message_id": opt(ID), "code": s(64)}),
}

ADVERT = obj(
    {
        "protocol_version": enum(PROTOCOL_VERSION),
        "type": enum("SERVICE_ADVERT"),
        "agent_key_id": KEY,
        "enc_key": s(64),
        "route": ID,
        "name": s(64),
        "service": s(64),
        "verifier": VERIFIER,
        "networks": lst(obj({"network": NETWORK, "asset": ASSET}), 8),
        "payee": s(128),
        "created_at": ts,
        "expires_at": ts,
        "description": s(500),
        "signature": hexs(64),
    }
)

INNER = obj(
    {
        "protocol_version": s(32),
        "message_id": ID,
        "session_id": ID,
        "sequence": i(0, 2**31),
        "type": s(32),
        "created_at": ts,
        "expires_at": ts,
        "sender_key_id": KEY,
        "reply_to": opt(ID),
        "previous_hash": opt(hexs(32)),
        "payload": anyobj,
        "signature": hexs(64),
    }
)


# ----------------------------------------------------------------- helpers


def terms_hash(terms: dict) -> str:
    return hash_obj(terms)


def agreement_hash(agreement: dict) -> str:
    return hash_obj({"domain": "pactmesh/agreement/v1", "agreement": agreement})


def message_hash(msg: dict) -> str:
    return hash_obj(msg)


def build_message(
    identity,
    *,
    type: str,
    session_id: str,
    sequence: int,
    payload: dict,
    ttl: int = 120,
    reply_to: str | None = None,
    previous_hash: str | None = None,
) -> dict:
    if type not in MESSAGE_TYPES:
        raise ProtocolError("UNKNOWN_TYPE", type)
    PAYLOADS[type](payload, "payload")
    t = now()
    msg = {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": random_id(),
        "session_id": session_id,
        "sequence": sequence,
        "type": type,
        "created_at": iso(t),
        "expires_at": iso(t + ttl),
        "sender_key_id": identity.key_id,
        "reply_to": reply_to,
        "previous_hash": previous_hash,
        "payload": payload,
    }
    return identity.sign_obj(msg)


def validate_message(msg: Any) -> dict:
    """Stateless validation: structure, version, type, payload, time window
    and self-signature. Binding the sender key to a known party, sequence and
    duplicate checks are stateful and happen in the runtime."""
    if not isinstance(msg, dict):
        raise ProtocolError("SCHEMA_INVALID", "message must be an object")
    if msg.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("UNSUPPORTED_VERSION", str(msg.get("protocol_version")))
    INNER(msg, "msg")
    if msg["type"] not in MESSAGE_TYPES or msg["type"] == "SERVICE_ADVERT":
        raise ProtocolError("UNKNOWN_TYPE", msg["type"])
    PAYLOADS[msg["type"]](msg["payload"], "payload")
    t = now()
    if parse_iso(msg["created_at"]) > t + CLOCK_SKEW:
        raise ProtocolError("NOT_YET_VALID")
    if parse_iso(msg["expires_at"]) < t:
        raise ProtocolError("EXPIRED")
    if not verify_obj(msg, msg["sender_key_id"]):
        raise ProtocolError("BAD_SIGNATURE")
    return msg


def build_advert(identity, *, route, name, service, verifier, networks, payee, ttl, description) -> dict:
    t = now()
    adv = {
        "protocol_version": PROTOCOL_VERSION,
        "type": "SERVICE_ADVERT",
        "agent_key_id": identity.key_id,
        "enc_key": identity.enc_public_b64,
        "route": route,
        "name": name,
        "service": service,
        "verifier": verifier,
        "networks": networks,
        "payee": payee,
        "created_at": iso(t),
        "expires_at": iso(t + ttl),
        "description": description,
    }
    return identity.sign_obj(adv)


def validate_advert(adv: Any) -> dict:
    ADVERT(adv, "advert")
    if parse_iso(adv["expires_at"]) < now():
        raise ProtocolError("EXPIRED", "advert")
    if not verify_obj(adv, adv["agent_key_id"]):
        raise ProtocolError("BAD_SIGNATURE", "advert")
    return adv
