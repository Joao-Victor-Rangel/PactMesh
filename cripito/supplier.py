"""Supplier agent: sells the ``stats-report`` service."""

from __future__ import annotations

from .crypto import decrypt_artifact, random_id
from .canonical import sha256_hex
from .ledger import escrow_id_for
from .ledger.sim import NETWORK, TEST_MINT
from .protocol import ProtocolError, agreement_hash, build_advert, terms_hash
from .runtime import Agent, Retry, agreement_signature_ok
from .transport import TransportUnavailable
from .util import iso, now, parse_iso
from . import verifier as stats

SERVICE = "stats-report"


class Supplier(Agent):
    role = "supplier"

    def __init__(self, home, name, transport, ledger, *, price: int, min_price: int, delivery_seconds: int,
                 description: str = "", behavior: str = "honest", advert_ttl: int = 3600, quote_ttl: int = 120):
        super().__init__(home, name, transport, ledger)
        self.price, self.min_price, self.delivery_seconds = price, min_price, delivery_seconds
        self.description, self.behavior = description, behavior
        self.advert_ttl, self.quote_ttl = advert_ttl, quote_ttl
        route = self.store.get_kv("advert_route")
        if not route:
            route = random_id()
            self.store.set_kv("advert_route", route)
        self.advert_route = route
        self.add_route(route)
        self._advert_at = 0

    def advert(self) -> dict:
        return build_advert(
            self.identity, route=self.advert_route, name=self.name, service=SERVICE, verifier=stats.VERIFIER,
            networks=[{"network": NETWORK, "asset": TEST_MINT}], payee=self.wallet.address, ttl=self.advert_ttl,
            description=self.description)

    def tick(self) -> None:
        if now() - self._advert_at > self.advert_ttl // 2:
            try:
                if self.transport.publish_advert(self.advert()):
                    self._advert_at = now()
            except TransportUnavailable:
                pass

    # ----------------------------------------------------------- handling

    def _peer(self, neg: dict) -> dict:
        return {"route": neg["data"]["reply_route"], "enc_key": neg["data"]["reply_enc_key"]}

    def _quote(self, neg: dict, price: int, round_: int, in_response_to: str | None) -> dict:
        d = neg["data"]
        terms = {
            "task_id": d["task_id"], "service": SERVICE, "verifier": stats.VERIFIER, "price": str(price),
            "asset": d["asset"], "network": d["network"], "delivery_seconds": self.delivery_seconds,
            "valid_until": iso(now() + self.quote_ttl), "payee": self.wallet.address,
            "supplier_key_id": self.identity.key_id, "dataset_sha256": d["dataset_sha256"],
        }
        payload = {"quote_id": random_id(), "round": round_, "in_response_to": in_response_to, "terms": terms,
                   "terms_hash": terms_hash(terms), "description": self.description}
        d.setdefault("quotes", {})[payload["quote_id"]] = {"terms": terms, "terms_hash": payload["terms_hash"]}
        d["round"] = round_
        self.store.save_negotiation(neg["id"], "QUOTED", d)
        self.send(neg["id"], self._peer(neg), neg["id"], "QUOTE", payload)
        self.event(neg["id"], "QUOTE_ISSUED", prev_state=neg["state"], new_state="QUOTED",
                   artifacts={"terms_hash": payload["terms_hash"]}, price=str(price), round=round_)
        return payload

    def handle(self, msg: dict) -> None:
        t, p, sid = msg["type"], msg["payload"], msg["session_id"]
        neg = self.store.get_negotiation(sid)
        if t == "TASK_REQUEST":
            if neg:
                raise ProtocolError("DUPLICATE_SESSION")
            if p["service"] != SERVICE or p["verifier"] != stats.VERIFIER:
                raise ProtocolError("SERVICE_UNSUPPORTED")
            if (p["network"], p["asset"]) != (NETWORK, TEST_MINT):
                raise ProtocolError("SETTLEMENT_UNSUPPORTED")
            data = {"task_id": p["task_id"], "buyer_key_id": msg["sender_key_id"], "reply_route": p["reply_route"],
                    "reply_enc_key": p["reply_enc_key"], "asset": p["asset"], "network": p["network"],
                    "dataset_sha256": p["dataset"]["sha256"], "requirements": p["requirements"]}
            self.store.create_negotiation(sid, "supplier", "REQUESTED", data)
            self.event(sid, "TASK_RECEIVED", prev_state=None, new_state="REQUESTED",
                       artifacts={"dataset_sha256": p["dataset"]["sha256"]})
            self._quote(self.store.get_negotiation(sid), self.price, 0, None)
            return
        if not neg:
            raise ProtocolError("UNKNOWN_SESSION")
        if msg["sender_key_id"] != neg["data"]["buyer_key_id"]:
            raise ProtocolError("SENDER_MISMATCH")
        d = neg["data"]
        if t == "COUNTEROFFER":
            if neg["state"] != "QUOTED":
                raise ProtocolError("STATE_INVALID")
            q = d.get("quotes", {}).get(p["quote_id"])
            if not q or q["terms_hash"] != p["terms_hash"]:
                raise ProtocolError("UNKNOWN_QUOTE")
            if p["round"] > 3:
                self.send(sid, self._peer(neg), sid, "DECLINE", {"ref": p["quote_id"], "reason_code": "ROUNDS_EXCEEDED"})
                return
            self.event(sid, "COUNTEROFFER_RECEIVED", price=p["price"], round=p["round"])
            self._quote(neg, max(int(p["price"]), self.min_price), p["round"], p["quote_id"])
        elif t == "ACCEPT":
            self._on_accept(neg, msg)
        elif t == "FUNDING_NOTICE":
            self._on_funding(neg, msg)
        elif t == "RECEIPT":
            if neg["state"] not in ("DELIVERED", "SETTLED", "DISPUTED"):
                raise ProtocolError("STATE_INVALID")
            esc = self.ledger.get_escrow(p["settlement"]["escrow_id"]) if self.ledger else None
            new = "SETTLED" if p["status"] == "ACCEPTED" and esc and esc["state"] == "RELEASED" else "DISPUTED"
            d["receipt"] = p
            self.store.save_negotiation(sid, new, d)
            self.event(sid, "RECEIPT_RECEIVED", prev_state=neg["state"], new_state=new,
                       escrow_state=esc["state"] if esc else None)
            self.make_batch()
        elif t in ("DECLINE", "CANCEL"):
            if neg["state"] in ("REQUESTED", "QUOTED", "AGREED"):
                self.store.save_negotiation(sid, "CANCELLED", d)
                self.event(sid, "CANCELLED", prev_state=neg["state"], new_state="CANCELLED", reason=p["reason_code"])
        elif t in ("ACK", "ERROR"):
            self.event(sid, f"{t}_RECEIVED", detail=p.get("code") or "")
        else:
            raise ProtocolError("UNEXPECTED_TYPE", t)

    def _on_accept(self, neg: dict, msg: dict) -> None:
        sid, d, p = neg["id"], neg["data"], msg["payload"]
        ag = p["agreement"]
        ah = agreement_hash(ag)
        if ah != p["agreement_hash"]:
            raise ProtocolError("AGREEMENT_HASH_MISMATCH")
        if neg["state"] == "AGREED" and d.get("agreement_hash") == ah:
            # duplicate ACCEPT: resend the same countersignature, no new effect
            self.send(sid, self._peer(neg), sid, "ACCEPT", {**p, "supplier_signature": d["supplier_signature"]})
            return
        if neg["state"] != "QUOTED":
            raise ProtocolError("STATE_INVALID")
        q = d.get("quotes", {}).get(p["quote_id"])
        if not q or q["terms_hash"] != p["terms_hash"] or ag["terms_hash"] != p["terms_hash"]:
            raise ProtocolError("UNKNOWN_QUOTE")
        terms = q["terms"]
        if parse_iso(terms["valid_until"]) < now():
            raise ProtocolError("QUOTE_EXPIRED")
        expect = {"task_id": terms["task_id"], "quote_id": p["quote_id"], "supplier_key_id": self.identity.key_id,
                  "buyer_key_id": d["buyer_key_id"], "payee": terms["payee"], "price": terms["price"],
                  "asset": terms["asset"], "network": terms["network"], "verifier": terms["verifier"],
                  "dataset_sha256": terms["dataset_sha256"]}
        for k, v in expect.items():
            if ag[k] != v:
                raise ProtocolError("AGREEMENT_TERMS_MISMATCH", k)
        if not agreement_signature_ok(ah, d["buyer_key_id"], p["buyer_signature"]):
            raise ProtocolError("BAD_SIGNATURE", "buyer agreement signature")
        sig = self.identity.sign(bytes.fromhex(ah))
        d.update(agreement=ag, agreement_hash=ah, buyer_signature=p["buyer_signature"], supplier_signature=sig)
        self.store.save_negotiation(sid, "AGREED", d)
        self.event(sid, "AGREEMENT_SIGNED", prev_state="QUOTED", new_state="AGREED", artifacts={"agreement_hash": ah})
        self.send(sid, self._peer(neg), sid, "ACCEPT", {**p, "supplier_signature": sig})

    def _on_funding(self, neg: dict, msg: dict) -> None:
        sid, d, p = neg["id"], neg["data"], msg["payload"]
        if neg["state"] == "DELIVERED" and d.get("delivery"):
            self.send(sid, self._peer(neg), sid, "DELIVERY", d["delivery"])  # idempotent re-delivery
            return
        if neg["state"] != "AGREED" or p["agreement_hash"] != d["agreement_hash"]:
            raise ProtocolError("STATE_INVALID")
        ag = d["agreement"]
        # Never work before the escrow is verifiably funded with the exact terms.
        if p["escrow_id"] != escrow_id_for(d["agreement_hash"]):
            raise ProtocolError("ESCROW_MISMATCH")
        esc = self.ledger.get_escrow(p["escrow_id"])
        if not esc or esc["state"] != "FUNDED":
            raise Retry()
        if (esc["amount"], esc["mint"], esc["payee"], esc["agreement_hash"]) != (
                ag["price"], ag["asset"], self.wallet.address, d["agreement_hash"]):
            raise ProtocolError("ESCROW_MISMATCH")
        acc = p["dataset_access"]
        try:
            blob = self.transport.get_blob(acc["blob_id"])
        except TransportUnavailable:
            raise Retry()
        data = decrypt_artifact(blob, acc)
        if sha256_hex(data) != ag["dataset_sha256"] or acc["sha256"] != ag["dataset_sha256"]:
            raise ProtocolError("DATASET_MISMATCH")
        req = d["requirements"]
        report = stats.compute_report(data, req["column"], req["percentiles"])
        if self.behavior == "bad_format":
            report.pop("stdev")
        elif self.behavior == "wrong_values":
            report["mean"] = "0.000000"
        delivery = {"agreement_hash": d["agreement_hash"], "report": report, "report_sha256": stats.report_hash(report)}
        d["delivery"] = delivery
        self.store.save_negotiation(sid, "DELIVERED", d)
        self.event(sid, "DELIVERED", prev_state="AGREED", new_state="DELIVERED",
                   artifacts={"report_sha256": delivery["report_sha256"], "dataset_sha256": ag["dataset_sha256"]},
                   escrow_id=p["escrow_id"])
        if self.behavior != "silent":
            self.send(sid, self._peer(neg), sid, "DELIVERY", delivery)
