"""Buyer agent: negotiation state machine, policy-gated execution,
escrow funding/release with reconciliation, verification and receipts."""

from __future__ import annotations

import csv
import io
import json

from . import verifier as stats
from .canonical import sha256_hex
from .crypto import encrypt_artifact, random_id, verify_obj
from .decision import Decision
from .ledger import LedgerError
from .policy import PolicyEngine
from .protocol import PROTOCOL_VERSION, ProtocolError, agreement_hash, validate_advert
from .runtime import Agent, agreement_signature_ok
from .supplier import SERVICE
from .transport import TransportUnavailable
from .util import iso, now, parse_iso

STATES = ("CREATED", "QUOTING", "NEGOTIATING", "AGREED", "FUNDING_PENDING", "FUNDED", "DELIVERED", "VERIFIED",
          "SETTLEMENT_PENDING", "SETTLED", "CANCELLED", "EXPIRED", "DISPUTED")
TRANSITIONS = {
    "CREATED": {"QUOTING", "CANCELLED", "EXPIRED"},
    "QUOTING": {"NEGOTIATING", "CANCELLED", "EXPIRED"},
    "NEGOTIATING": {"AGREED", "CANCELLED", "EXPIRED"},
    "AGREED": {"FUNDING_PENDING", "CANCELLED"},
    "FUNDING_PENDING": {"FUNDED", "CANCELLED", "EXPIRED"},
    "FUNDED": {"DELIVERED", "EXPIRED", "DISPUTED"},
    "DELIVERED": {"VERIFIED", "DISPUTED"},
    "VERIFIED": {"SETTLEMENT_PENDING"},
    "SETTLEMENT_PENDING": {"SETTLED"},
    "SETTLED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
    "DISPUTED": set(),
}
ACTIVE = tuple(s for s in STATES if s not in ("CANCELLED", "EXPIRED", "DISPUTED", "SETTLED"))
COUNTERSIGN_TIMEOUT = 60
COUNTER_TIMEOUT = 45
FUNDING_SLACK = 120
ESCROW_GRACE = 60
LOST_TX_AFTER = 60


class StateError(RuntimeError):
    pass


class Buyer(Agent):
    role = "buyer"

    def __init__(self, home, name, transport, ledger, *, engine, policy: dict | None = None,
                 supplier_allowlist: list[str] | None = None):
        super().__init__(home, name, transport, ledger)
        self.engine = engine
        self.policy = PolicyEngine(self.store, self.identity, policy)
        self.allowlist = supplier_allowlist
        (self.home / "artifacts").mkdir(exist_ok=True)

    # ----------------------------------------------------------- helpers

    def _transition(self, neg: dict, new: str, **event) -> dict:
        old = neg["state"]
        if new not in TRANSITIONS[old]:
            raise StateError(f"{old} -> {new} not allowed")
        with self.store.tx():
            self.store.save_negotiation(neg["id"], new, neg["data"])
            self.event(neg["id"], "STATE", prev_state=old, new_state=new,
                       policy_hash=self.policy.hash, engine=self.engine.model_id, **event)
        neg["state"] = new
        return neg

    def _save(self, neg: dict) -> None:
        self.store.save_negotiation(neg["id"], neg["state"], neg["data"])

    def _peer(self, neg: dict, sid: str) -> dict:
        s = neg["data"]["sessions"][sid]
        return {"route": s["route"], "enc_key": s["enc_key"]}

    def _artifact_path(self, sha: str):
        return self.home / "artifacts" / sha

    def _timeline(self, neg: dict, **entry) -> None:
        self.store.put_record("timeline", random_id(), {"at": iso(now()), **entry}, neg["id"])

    # ---------------------------------------------------------- creation

    def create_task(self, *, csv_bytes: bytes, column: str, budget: int, percentiles=(25, 50, 75, 90),
                    max_delivery_seconds: int = 120, quote_window_seconds: int = 6) -> str:
        rows = sum(1 for _ in csv.reader(io.StringIO(csv_bytes.decode()))) - 1
        stats.load_column(csv_bytes, column)  # reject an unusable dataset up front
        sha = sha256_hex(csv_bytes)
        self._artifact_path(sha).write_bytes(csv_bytes)
        task_id, reply_route = random_id(), random_id()
        task = {
            "task_id": task_id, "service": SERVICE, "verifier": stats.VERIFIER,
            "requirements": {"format": "json", "column": column, "percentiles": list(percentiles),
                             "max_delivery_seconds": max_delivery_seconds},
            "dataset": {"sha256": sha, "rows": rows, "bytes": len(csv_bytes)},
            "asset": self.ledger.test_mint, "network": self.ledger.network, "budget": str(budget),
            "quote_deadline": iso(now() + quote_window_seconds),
        }
        data = {"task": task, "reply_route": reply_route, "sessions": {}, "adverts": {}, "quotes": {},
                "blocked": {}, "round": 0, "counter_pending": None}
        with self.store.tx():
            self.store.create_negotiation(task_id, "buyer", "CREATED", data)
            self.event(task_id, "TASK_CREATED", prev_state=None, new_state="CREATED",
                       artifacts={"dataset_sha256": sha}, budget=str(budget), policy_hash=self.policy.hash)
        self.add_route(reply_route)
        return task_id

    def cancel(self, task_id: str, reason: str = "USER_CANCELLED") -> dict:
        neg = self.store.get_negotiation(task_id)
        if not neg:
            raise KeyError(task_id)
        if "CANCELLED" not in TRANSITIONS[neg["state"]]:
            return {"state": neg["state"], "cancelled": False, "reason": "funded work continues to settlement/refund"}
        with self.store.tx():
            for sid, s in neg["data"]["sessions"].items():
                if s["status"] != "declined":
                    self.send(neg["id"], self._peer(neg, sid), sid, "CANCEL", {"ref": task_id, "reason_code": reason})
            self._transition(neg, "CANCELLED", reason=reason)
        return {"state": "CANCELLED", "cancelled": True}

    # --------------------------------------------------------- inbound

    def _session(self, neg: dict, msg: dict) -> dict:
        s = neg["data"]["sessions"].get(msg["session_id"])
        if not s:
            raise ProtocolError("UNKNOWN_SESSION")
        if s["supplier_key_id"] != msg["sender_key_id"]:
            raise ProtocolError("SENDER_MISMATCH")
        return s

    def _neg_for_session(self, sid: str) -> dict:
        idx = self.store.get_kv("session_index", {})
        neg = self.store.get_negotiation(idx.get(sid, ""))
        if not neg:
            raise ProtocolError("UNKNOWN_SESSION")
        return neg

    def handle(self, msg: dict) -> None:
        neg = self._neg_for_session(msg["session_id"])
        s = self._session(neg, msg)
        d, p, t, sid = neg["data"], msg["payload"], msg["type"], msg["session_id"]
        if t == "QUOTE":
            terms = p["terms"]
            if terms["task_id"] != d["task"]["task_id"]:
                raise ProtocolError("TASK_MISMATCH")
            if neg["state"] not in ("QUOTING", "NEGOTIATING"):
                raise ProtocolError("STATE_INVALID")
            d["quotes"][p["quote_id"]] = {"session_id": sid, "msg": msg, "supplier": s["name"],
                                          "received_at": now()}
            s["latest_quote"] = p["quote_id"]
            s["status"] = "quoted"
            cp = d.get("counter_pending")
            if cp and cp["session_id"] == sid:
                d["counter_pending"] = None
            self._save(neg)
            self.event(neg["id"], "QUOTE_RECEIVED", supplier=s["name"], artifacts={"terms_hash": p["terms_hash"]},
                       price=terms["price"], round=p["round"])
        elif t == "ACCEPT":
            pend = d.get("awaiting_countersign")
            if not pend or pend["session_id"] != sid or p["agreement_hash"] != d.get("agreement_hash"):
                if neg["state"] != "NEGOTIATING" and p["agreement_hash"] == d.get("agreement_hash"):
                    return  # duplicate countersignature, already applied
                raise ProtocolError("UNEXPECTED_ACCEPT")
            if not agreement_signature_ok(d["agreement_hash"], s["supplier_key_id"], p.get("supplier_signature")):
                raise ProtocolError("BAD_SIGNATURE", "supplier agreement signature")
            d["agreement_signatures"]["supplier"] = p["supplier_signature"]
            d["awaiting_countersign"] = None
            with self.store.tx():
                self._transition(neg, "AGREED", artifacts={"agreement_hash": d["agreement_hash"]})
                for osid, os_ in d["sessions"].items():
                    if osid != sid and os_["status"] != "declined":
                        os_["status"] = "declined"
                        self.send(neg["id"], self._peer(neg, osid), osid, "DECLINE",
                                  {"ref": d["task"]["task_id"], "reason_code": "OTHER_SUPPLIER_SELECTED"})
                self._save(neg)
        elif t == "DECLINE":
            s["status"] = "declined"
            cp = d.get("counter_pending")
            if cp and cp["session_id"] == sid:
                d["counter_pending"] = None
            self._save(neg)
            self.event(neg["id"], "DECLINE_RECEIVED", supplier=s["name"], reason=p["reason_code"])
        elif t == "DELIVERY":
            self._on_delivery(neg, s, msg)
        elif t in ("ACK", "ERROR", "CANCEL"):
            self.event(neg["id"], f"{t}_RECEIVED", supplier=s["name"], detail=p.get("code") or p.get("reason_code") or "")
        else:
            raise ProtocolError("UNEXPECTED_TYPE", t)

    def _on_delivery(self, neg: dict, s: dict, msg: dict) -> None:
        d, p, sid = neg["data"], msg["payload"], msg["session_id"]
        if sid != d.get("chosen_session") or p["agreement_hash"] != d.get("agreement_hash"):
            raise ProtocolError("AGREEMENT_MISMATCH")
        if stats.report_hash(p["report"]) != p["report_sha256"]:
            raise ProtocolError("REPORT_HASH_MISMATCH")
        if neg["state"] != "FUNDED":
            if d.get("delivery", {}).get("report_sha256") == p["report_sha256"]:
                # duplicate delivery: same ACK and (if issued) the same receipt, no new effect
                self.send(neg["id"], self._peer(neg, sid), sid, "ACK", {"ack_message_id": msg["message_id"]})
                if d.get("receipt_msg"):
                    self.send(neg["id"], self._peer(neg, sid), sid, "RECEIPT", d["receipt_msg"])
                return
            raise ProtocolError("STATE_INVALID")
        d["delivery"] = {"report": p["report"], "report_sha256": p["report_sha256"], "message_id": msg["message_id"]}
        with self.store.tx():
            self._transition(neg, "DELIVERED", artifacts={"report_sha256": p["report_sha256"]})
            self.send(neg["id"], self._peer(neg, sid), sid, "ACK", {"ack_message_id": msg["message_id"]})

    # ------------------------------------------------------------- ticks

    def tick(self) -> None:
        for neg in self.store.list_negotiations(ACTIVE + ("CANCELLED", "EXPIRED")):
            if neg["data"].get("closed"):
                continue
            try:
                getattr(self, f"_t_{neg['state'].lower()}")(neg)
            except LedgerError as e:
                self._note(neg, "LEDGER_UNAVAILABLE" if e.code == "RPC_UNAVAILABLE" else e.code)
            except TransportUnavailable as e:
                self._note(neg, "TRANSPORT_UNAVAILABLE", str(e))

    def _note(self, neg: dict, code: str, detail: str = "") -> None:
        if neg["data"].get("last_note") != code:
            neg["data"]["last_note"] = code
            self._save(neg)
            self.event(neg["id"], "PENDING", code=code, detail=detail[:200])

    def _t_created(self, neg: dict) -> None:
        d = neg["data"]
        task = d["task"]
        found = {}
        for adv in self.transport.fetch_adverts():
            try:
                validate_advert(adv)
            except ProtocolError:
                continue
            if adv["service"] != task["service"] or adv["verifier"] != task["verifier"]:
                continue
            if {"network": task["network"], "asset": task["asset"]} not in adv["networks"]:
                continue
            if self.allowlist is not None and adv["agent_key_id"] not in self.allowlist:
                continue
            found[adv["agent_key_id"]] = adv
        if not found:
            if now() > parse_iso(task["quote_deadline"]):
                self._transition(neg, "EXPIRED", reason="NO_SUPPLIERS")
            return
        idx = self.store.get_kv("session_index", {})
        with self.store.tx():
            for key, adv in found.items():
                sid = random_id()
                idx[sid] = neg["id"]
                d["adverts"][key] = adv
                d["sessions"][sid] = {"supplier_key_id": key, "name": adv["name"], "route": adv["route"],
                                      "enc_key": adv["enc_key"], "payee": adv["payee"], "status": "requested"}
                public_task = {k: task[k] for k in ("task_id", "service", "verifier", "requirements", "dataset",
                                                    "asset", "network", "quote_deadline")}
                # The budget is private and never leaves the buyer.
                self.send(neg["id"], self._peer(neg, sid), sid, "TASK_REQUEST",
                          {**public_task, "reply_route": d["reply_route"],
                           "reply_enc_key": self.identity.enc_public_b64})
            self.store.set_kv("session_index", idx)
            self._transition(neg, "QUOTING", suppliers=len(found))

    def _t_quoting(self, neg: dict) -> None:
        d = neg["data"]
        responded = all(s["status"] != "requested" for s in d["sessions"].values())
        if responded or now() >= parse_iso(d["task"]["quote_deadline"]):
            if d["quotes"]:
                self._transition(neg, "NEGOTIATING", quotes=len(d["quotes"]))
            else:
                self._transition(neg, "EXPIRED", reason="NO_QUOTES")

    def _options(self, neg: dict) -> list[dict]:
        d, task = neg["data"], neg["data"]["task"]
        req = task["requirements"]
        out = []
        for sid, s in d["sessions"].items():
            qid = s.get("latest_quote")
            if s["status"] == "declined" or not qid or qid in d["blocked"]:
                continue
            q = d["quotes"][qid]["msg"]["payload"]
            terms = q["terms"]
            # Mandatory requirements are checked before preferences. The
            # budget is NOT filtered here: it is enforced by the policy.
            if terms["verifier"] != task["verifier"] or terms["delivery_seconds"] > req["max_delivery_seconds"]:
                continue
            if parse_iso(terms["valid_until"]) < now():
                continue
            out.append({"quote_id": qid, "supplier": s["name"], "price": terms["price"],
                        "delivery_seconds": terms["delivery_seconds"], "description": q["description"],
                        "counter_prices": self.policy.counter_prices(int(terms["price"])),
                        "final": q["round"] >= int(self.policy.config["max_counter_rounds"])})
        return out

    def _t_negotiating(self, neg: dict) -> None:
        d = neg["data"]
        if d.get("needs_human"):
            return
        pend = d.get("awaiting_countersign")
        if pend:
            if now() - pend["since"] > COUNTERSIGN_TIMEOUT:
                d["blocked"][pend["quote_id"]] = "NO_COUNTERSIGNATURE"
                d["awaiting_countersign"] = None
                self.store.set_reservation(neg["id"], "released")
                self._save(neg)
            return
        cp = d.get("counter_pending")
        if cp:
            if now() - cp["since"] <= COUNTER_TIMEOUT:
                return
            d["counter_pending"] = None
            d["sessions"][cp["session_id"]]["status"] = "declined"
            self._save(neg)
        max_rounds = int(self.policy.config["max_counter_rounds"])
        for _ in range(6):
            options = self._options(neg)
            if not options:
                self._close_without_contract(neg, "NO_ELIGIBLE_PROPOSAL")
                return
            state = {"negotiation_id": neg["id"], "round": d["round"], "max_rounds": max_rounds,
                     "budget": d["task"]["budget"], "max_delivery_seconds": d["task"]["requirements"]["max_delivery_seconds"]}
            try:
                dec = self.engine.decide(state, options)
            except Exception:
                dec = Decision("ABSTAIN", status="unavailable", model_id=getattr(self.engine, "model_id", "?"))
            rec = {"model": dec.to_dict(), "options": [{k: o[k] for k in ("quote_id", "supplier", "price", "delivery_seconds", "description")} for o in options]}
            self.event(neg["id"], "DECISION", action=dec.action, quote_id=dec.quote_id, model_id=dec.model_id,
                       model_revision=dec.model_revision, status=dec.status, latency_ms=dec.latency_ms)
            if dec.action == "ACCEPT":
                qrec = d["quotes"][dec.quote_id]
                adv = d["adverts"][d["sessions"][qrec["session_id"]]["supplier_key_id"]]
                auth = self.policy.authorize_accept(neg, qrec["msg"], adv)
                self.event(neg["id"], "POLICY", action="ACCEPT_QUOTE", allowed=auth["allowed"], code=auth["code"],
                           rule=auth["rule"], policy_hash=auth["policy_hash"], result_id=auth["result_id"])
                if auth["allowed"]:
                    self._accept(neg, qrec, auth)
                    self._timeline(neg, **rec, policy=_p(auth), executed=f"ACCEPT sent to {qrec['supplier']} @ {qrec['msg']['payload']['terms']['price']}")
                    return
                d["blocked"][dec.quote_id] = auth["code"]
                self._save(neg)
                self._timeline(neg, **rec, policy=_p(auth), executed="none (blocked by policy)")
                continue
            if dec.action == "COUNTEROFFER":
                qrec = d["quotes"][dec.quote_id]
                auth = self.policy.authorize_counteroffer(neg, qrec["msg"], dec.counter_price, d["round"] + 1)
                self.event(neg["id"], "POLICY", action="SEND_COUNTEROFFER", allowed=auth["allowed"], code=auth["code"],
                           rule=auth["rule"], policy_hash=auth["policy_hash"], result_id=auth["result_id"])
                if auth["allowed"]:
                    p = qrec["msg"]["payload"]
                    with self.store.tx():
                        d["round"] += 1
                        d["counter_pending"] = {"session_id": qrec["session_id"], "quote_id": p["quote_id"], "since": now()}
                        self.send(neg["id"], self._peer(neg, qrec["session_id"]), qrec["session_id"], "COUNTEROFFER",
                                  {"quote_id": p["quote_id"], "terms_hash": p["terms_hash"],
                                   "price": str(dec.counter_price), "round": d["round"]})
                        self._save(neg)
                    self._timeline(neg, **rec, policy=_p(auth), executed=f"COUNTEROFFER sent to {qrec['supplier']} @ {dec.counter_price}")
                    return
                self._timeline(neg, **rec, policy=_p(auth), executed="none (blocked by policy)")
                if auth["code"] == "ROUNDS_EXCEEDED":
                    d["round"] = max_rounds
                    continue
                d["needs_human"] = True
                self._save(neg)
                return
            if dec.action == "REJECT":
                self._timeline(neg, **rec, policy=None, executed="close without contract")
                self._close_without_contract(neg, "DECISION_REJECT")
                return
            # ABSTAIN / REQUEST_INFO: the system must not continue automatically.
            d["needs_human"] = True
            self._save(neg)
            self._timeline(neg, **rec, policy=None, executed="none (abstained: human review required)")
            return
        d["needs_human"] = True
        self._save(neg)

    def _close_without_contract(self, neg: dict, reason: str) -> None:
        d = neg["data"]
        with self.store.tx():
            for sid, s in d["sessions"].items():
                if s["status"] != "declined":
                    s["status"] = "declined"
                    self.send(neg["id"], self._peer(neg, sid), sid, "DECLINE",
                              {"ref": d["task"]["task_id"], "reason_code": reason})
            self._transition(neg, "CANCELLED", reason=reason)

    def _accept(self, neg: dict, qrec: dict, auth: dict) -> None:
        d, p = neg["data"], qrec["msg"]["payload"]
        terms = p["terms"]
        params = {"quote_id": p["quote_id"], "terms_hash": p["terms_hash"], "amount": terms["price"]}
        code = self.policy.check_authorization(auth, params)  # revalidate right before signing
        if code != "OK":
            d["blocked"][p["quote_id"]] = code
            self.store.set_reservation(neg["id"], "released")
            self._save(neg)
            return
        delivery_deadline = now() + terms["delivery_seconds"] + FUNDING_SLACK
        agreement = {
            "protocol_version": PROTOCOL_VERSION, "task_id": terms["task_id"], "quote_id": p["quote_id"],
            "terms_hash": p["terms_hash"], "buyer_key_id": self.identity.key_id,
            "supplier_key_id": terms["supplier_key_id"], "payer": self.ledger.address, "payee": terms["payee"],
            "price": terms["price"], "asset": terms["asset"], "network": terms["network"],
            "verifier": terms["verifier"], "dataset_sha256": terms["dataset_sha256"],
            "delivery_deadline": iso(delivery_deadline), "escrow_deadline": iso(delivery_deadline + ESCROW_GRACE),
        }
        ah = agreement_hash(agreement)
        sig = self.identity.sign(bytes.fromhex(ah))
        with self.store.tx():
            d.update(agreement=agreement, agreement_hash=ah, agreement_signatures={"buyer": sig},
                     chosen_session=qrec["session_id"], accept_auth=auth["result_id"],
                     awaiting_countersign={"session_id": qrec["session_id"], "quote_id": p["quote_id"], "since": now()})
            self._save(neg)
            self.event(neg["id"], "AGREEMENT_PROPOSED", artifacts={"agreement_hash": ah, "terms_hash": p["terms_hash"]},
                       price=terms["price"], policy_result=auth["result_id"])
            self.send(neg["id"], self._peer(neg, qrec["session_id"]), qrec["session_id"], "ACCEPT",
                      {"quote_id": p["quote_id"], "terms_hash": p["terms_hash"], "agreement": agreement,
                       "agreement_hash": ah, "buyer_signature": sig, "supplier_signature": None})

    # ------------------------------------------------------- settlement

    def _fund_params(self, d: dict) -> dict:
        ag = d["agreement"]
        return {"amount": ag["price"], "payee": ag["payee"], "network": ag["network"], "asset": ag["asset"],
                "escrow_id": self.ledger.escrow_id_for(d["agreement_hash"])}

    def _t_agreed(self, neg: dict) -> None:
        d = neg["data"]
        params = self._fund_params(d)
        esc = self.ledger.get_escrow(params["escrow_id"])  # reconcile before any effect
        if esc is None and not self.store.get_effect(neg["id"], "ESCROW_CREATE"):
            auth = self.policy.authorize_fund(neg, params)
            self.event(neg["id"], "POLICY", action="FUND_ESCROW", allowed=auth["allowed"], code=auth["code"],
                       rule=auth["rule"], policy_hash=auth["policy_hash"], result_id=auth["result_id"])
            if not auth["allowed"]:
                self.store.set_reservation(neg["id"], "released")
                self._transition(neg, "CANCELLED", reason=auth["code"])
                return
            if self.policy.check_authorization(auth, params) != "OK":
                return
            st = self.ledger.create_escrow(d["agreement"], d["agreement_hash"], parse_iso(d["agreement"]["escrow_deadline"]))
            self.store.record_effect(neg["id"], "ESCROW_CREATE", st)
            if not st["ok"]:
                self.store.set_reservation(neg["id"], "released")
                self._transition(neg, "CANCELLED", reason=f"ESCROW_CREATE_FAILED:{st['err']}")
                return
        d["escrow_id"] = params["escrow_id"]
        self._transition(neg, "FUNDING_PENDING", escrow_id=params["escrow_id"], settlement_network=self.ledger.network,
                         simulated=self.ledger.simulated)

    def _t_funding_pending(self, neg: dict) -> None:
        d = neg["data"]
        params = self._fund_params(d)
        esc = self.ledger.get_escrow(params["escrow_id"])
        if esc is None:
            return
        eff = self.store.get_effect(neg["id"], "FUND_ESCROW")
        if esc["state"] == "CREATED":
            if eff:
                st = self.ledger.tx_status(eff["signature"])
                if st and not st["ok"]:
                    self.store.set_reservation(neg["id"], "released")
                    self._transition(neg, "CANCELLED", reason=f"FUNDING_FAILED:{st['err']}")
                    return
                if st is None and now() - eff.get("_at", now()) > LOST_TX_AFTER:
                    # Lost/expired tx: escrow verified still CREATED, safe to replace.
                    self.store.q("DELETE FROM effects WHERE negotiation_id=? AND action='FUND_ESCROW'", (neg["id"],))
                return
            auth = self.policy.authorize_fund(neg, params)
            self.event(neg["id"], "POLICY", action="FUND_ESCROW", allowed=auth["allowed"], code=auth["code"],
                       rule=auth["rule"], policy_hash=auth["policy_hash"], result_id=auth["result_id"])
            if not auth["allowed"] or self.policy.check_authorization(auth, params) != "OK":
                if auth["code"] == "PAUSED":
                    self._note(neg, "PAUSED")
                    return
                self.store.set_reservation(neg["id"], "released")
                self._transition(neg, "CANCELLED", reason=auth["code"])
                return
            st = self.ledger.fund_escrow(params["escrow_id"], params["amount"], params["asset"])
            self.store.record_effect(neg["id"], "FUND_ESCROW", {**st, "_at": now()})
            return
        if esc["state"] != "FUNDED":
            return
        if eff:
            st = self.ledger.tx_status(eff["signature"])
            if not st or st["confirmation"] == "processed":
                return  # wait for confirmation before authorizing work
        share = self.policy.authorize_share_data(neg, "stats-report", esc["state"])
        if not share["allowed"]:
            self._note(neg, share["code"])
            return
        sha = d["task"]["dataset"]["sha256"]
        blob, access = encrypt_artifact(self._artifact_path(sha).read_bytes())
        blob_id = random_id()
        replicas = self.transport.put_blob(blob_id, blob)
        sid = d["chosen_session"]
        with self.store.tx():
            self.store.set_reservation(neg["id"], "consumed")
            self.send(neg["id"], self._peer(neg, sid), sid, "FUNDING_NOTICE", {
                "agreement_hash": d["agreement_hash"], "escrow_id": params["escrow_id"],
                "tx_ref": eff["signature"] if eff else "reconciled", "amount": params["amount"],
                "asset": params["asset"], "network": params["network"],
                "dataset_access": {"blob_id": blob_id, **access, "sha256": sha, "purpose": "stats-report"}})
            self._transition(neg, "FUNDED", escrow_id=params["escrow_id"], blob_replicas=replicas,
                             artifacts={"dataset_sha256": sha}, policy_result=share["result_id"])

    def _t_funded(self, neg: dict) -> None:
        if now() > parse_iso(neg["data"]["agreement"]["delivery_deadline"]):
            sid = neg["data"]["chosen_session"]
            with self.store.tx():
                self.send(neg["id"], self._peer(neg, sid), sid, "CANCEL",
                          {"ref": neg["data"]["task"]["task_id"], "reason_code": "DELIVERY_TIMEOUT"})
                self._transition(neg, "EXPIRED", reason="DELIVERY_TIMEOUT")

    def _t_delivered(self, neg: dict) -> None:
        d = neg["data"]
        req = d["task"]["requirements"]
        csv_bytes = self._artifact_path(d["task"]["dataset"]["sha256"]).read_bytes()
        ok, errors = stats.verify_report(d["delivery"]["report"], csv_bytes, req["column"], req["percentiles"])
        d["verification"] = {"verifier": stats.VERIFIER, "ok": ok, "errors": errors[:32]}
        if ok:
            self._transition(neg, "VERIFIED", verification_ok=True)
            return
        # Failed verification: no release. Freeze escrow and preserve evidence.
        if not self.store.get_effect(neg["id"], "DISPUTE"):
            st = self.ledger.dispute(d["escrow_id"])
            self.store.record_effect(neg["id"], "DISPUTE", st)
        self.store.set_reservation(neg["id"], "consumed")
        self._transition(neg, "DISPUTED", verification_ok=False, errors=len(errors))
        self._issue_receipt(self.store.get_negotiation(neg["id"]), "REJECTED")

    def _t_verified(self, neg: dict) -> None:
        d = neg["data"]
        ag = d["agreement"]
        params = {"amount": ag["price"], "payee": ag["payee"], "escrow_id": d["escrow_id"]}
        esc = self.ledger.get_escrow(d["escrow_id"])
        if esc and esc["state"] == "RELEASED":
            self._transition(neg, "SETTLEMENT_PENDING", reconciled=True)
            return
        auth = self.policy.authorize_release(neg, params, d["verification"]["ok"])
        self.event(neg["id"], "POLICY", action="RELEASE_PAYMENT", allowed=auth["allowed"], code=auth["code"],
                   rule=auth["rule"], policy_hash=auth["policy_hash"], result_id=auth["result_id"])
        if not auth["allowed"] or self.policy.check_authorization(auth, params) != "OK":
            self._note(neg, auth["code"])
            return
        st = self.ledger.release(d["escrow_id"], ag["payee"])
        self.store.record_effect(neg["id"], "RELEASE_PAYMENT", st)
        self._transition(neg, "SETTLEMENT_PENDING", artifacts={"release_tx": st["signature"]})

    def _t_settlement_pending(self, neg: dict) -> None:
        d = neg["data"]
        esc = self.ledger.get_escrow(d["escrow_id"])
        eff = self.store.get_effect(neg["id"], "RELEASE_PAYMENT")
        st = self.ledger.tx_status(eff["signature"]) if eff else None
        if esc and esc["state"] == "RELEASED" and (st is None or st["confirmation"] == "finalized"):
            d["settlement"] = {"escrow_state": esc["state"], "confirmation": st["confirmation"] if st else "reconciled"}
            self._transition(neg, "SETTLED", confirmation=d["settlement"]["confirmation"])
            self._issue_receipt(self.store.get_negotiation(neg["id"]), "ACCEPTED")

    def _t_settled(self, neg: dict) -> None:
        if not neg["data"].get("receipt"):
            self._issue_receipt(neg, "ACCEPTED")

    def _cleanup(self, neg: dict) -> None:
        d = neg["data"]
        eid = d.get("escrow_id")
        esc = self.ledger.get_escrow(eid) if eid else None
        if esc and esc["state"] in ("CREATED", "FUNDED"):
            if now() < esc["deadline"]:
                return  # refund only after the agreed escrow deadline
            if not self.store.get_effect(neg["id"], "REFUND"):
                st = self.ledger.refund(eid)
                self.store.record_effect(neg["id"], "REFUND", st)
                self.event(neg["id"], "REFUND", artifacts={"refund_tx": st["signature"]}, ok=st["ok"])
            return
        self.store.set_reservation(neg["id"], "released")
        d["closed"] = True
        self._save(neg)
        self.event(neg["id"], "CLOSED", final_state=neg["state"], escrow_state=esc["state"] if esc else None)
        self.make_batch()

    _t_cancelled = _cleanup
    _t_expired = _cleanup

    # ----------------------------------------------------------- receipts

    def _issue_receipt(self, neg: dict, status: str) -> None:
        d = neg["data"]
        batch = self.make_batch()
        if batch is None:
            last = self.store.q("SELECT batch_id FROM events ORDER BY seq DESC LIMIT 1")
            batch = self.batch(last[0]["batch_id"])
        if not batch["anchor"]:
            self.anchor_batch(batch["batch_id"])
            batch = self.batch(batch["batch_id"])
        fund = self.store.get_effect(neg["id"], "FUND_ESCROW") or {}
        rel = self.store.get_effect(neg["id"], "RELEASE_PAYMENT") or {}
        esc = self.ledger.get_escrow(d["escrow_id"]) or {}
        receipt = {
            "type": "CRIPITO_RECEIPT", "protocol_version": PROTOCOL_VERSION, "task_id": d["task"]["task_id"],
            "status": status, "agreement": d["agreement"], "agreement_hash": d["agreement_hash"],
            "signatures": d["agreement_signatures"],
            "artifacts": {"dataset_sha256": d["task"]["dataset"]["sha256"],
                          "terms_hash": d["agreement"]["terms_hash"],
                          "report_sha256": d.get("delivery", {}).get("report_sha256")},
            "verification": d["verification"],
            "settlement": {"network": self.ledger.network, "simulated": self.ledger.simulated,
                           "escrow_id": d["escrow_id"], "fund_tx": fund.get("signature"),
                           "release_tx": rel.get("signature"), "escrow_state": esc.get("state")},
            "evidence_batch": {k: batch[k] for k in ("batch_id", "root", "version", "size")} | {"anchor": batch["anchor"]},
            "policy_hash": self.policy.hash, "issued_at": iso(now()), "buyer_key_id": self.identity.key_id,
        }
        receipt = self.identity.sign_obj(receipt, "receipt_signature")
        d["receipt"] = receipt
        sid = d["chosen_session"]
        d["receipt_msg"] = {"agreement_hash": d["agreement_hash"], "status": status,
                            "report_sha256": d.get("delivery", {}).get("report_sha256"),
                            "verification": d["verification"],
                            "settlement": {"escrow_id": d["escrow_id"], "tx_ref": rel.get("signature"),
                                           "state": esc.get("state", "UNKNOWN")}}
        with self.store.tx():
            self._save(neg)
            self.send(neg["id"], self._peer(neg, sid), sid, "RECEIPT", d["receipt_msg"])

    def evidence_package(self, task_id: str) -> dict:
        neg = self.store.get_negotiation(task_id)
        if not neg or not neg["data"].get("receipt"):
            raise KeyError(task_id)
        receipt = neg["data"]["receipt"]
        bid = receipt["evidence_batch"]["batch_id"]
        disclosed = [e for e in self.disclose(task_id) if e["batch_id"] == bid]
        return {"receipt": receipt, "disclosed_events": disclosed,
                "note": "Selective disclosure: only events of this negotiation; other events stay private."}

    # ---------------------------------------------------------- reporting

    def summary(self, task_id: str) -> dict:
        neg = self.store.get_negotiation(task_id)
        if not neg:
            raise KeyError(task_id)
        d = neg["data"]
        quotes = []
        for qid, q in d["quotes"].items():
            p = q["msg"]["payload"]
            quotes.append({"quote_id": qid, "supplier": q["supplier"], "price": p["terms"]["price"],
                           "delivery_seconds": p["terms"]["delivery_seconds"], "round": p["round"],
                           "description": p["description"], "blocked": d["blocked"].get(qid),
                           "signature_ok": verify_obj(q["msg"], q["msg"]["sender_key_id"])})
        return {
            "task_id": task_id, "state": neg["state"], "budget": d["task"]["budget"],
            "requirements": d["task"]["requirements"], "dataset": d["task"]["dataset"],
            "suppliers": [{"name": s["name"], "status": s["status"]} for s in d["sessions"].values()],
            "quotes": quotes, "round": d["round"], "needs_human": bool(d.get("needs_human")),
            "timeline": self.store.records("timeline", task_id),
            "agreement": d.get("agreement"), "verification": d.get("verification"),
            "receipt": d.get("receipt"), "last_note": d.get("last_note"),
            "events": [{"seq": e["event"]["seq"], "type": e["event"]["type"], "at": e["event"]["at"],
                        "new_state": e["event"].get("new_state"), "code": e["event"].get("code")}
                       for e in self.store.events(task_id)],
        }


def _p(auth: dict) -> dict:
    return {"allowed": auth["allowed"], "code": auth["code"], "rule": auth["rule"],
            "policy_version": auth["policy_version"], "result_id": auth["result_id"]}


__all__ = ["Buyer", "STATES", "TRANSITIONS", "StateError", "json"]
