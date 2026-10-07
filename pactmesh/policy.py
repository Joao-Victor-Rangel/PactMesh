"""Deterministic policy engine: the user's explicit control.

The decision engine only *recommends*. Every executable action is checked
here against the versioned (hashed) policy and persisted state. An
allowed result is a short-lived authorization bound to the exact action
parameters; executors must re-validate it immediately before signing.
"""

from __future__ import annotations

import copy

from .canonical import hash_obj
from .crypto import random_id, verify_obj
from .protocol import terms_hash as compute_terms_hash
from .util import iso, now, parse_iso

DEFAULT_POLICY = {
    "version": "2026-10-07.1",
    "budget_total": "100",
    "max_per_task": "100",
    "allowed_settlement": [{"network": "pactmesh-sim-devnet", "asset": "CRPT-TEST"}],
    "allowed_verifiers": [{"name": "pactmesh.stats", "version": "1.0"}],
    "data_purposes": ["stats-report"],
    "max_counter_rounds": 3,
    "authorization_ttl_seconds": 30,
    # Counteroffer prices come from a bounded grid (fractions of the quote).
    "counter_grid_percent": [90, 85, 80],
}

# Block codes are part of the documented protocol surface.
CODES = {
    "OK": "authorized",
    "PAUSED": "emergency stop / new contracts paused",
    "BUDGET_EXCEEDED": "price plus reservations exceeds authorized budget",
    "ASSET_NOT_ALLOWED": "network/asset pair not in the allow-list",
    "PAYEE_MISMATCH": "recipient differs from signed agreement or advert",
    "QUOTE_SIGNATURE_INVALID": "quote not signed by the advertised supplier key",
    "QUOTE_EXPIRED": "quote validity elapsed",
    "QUOTE_INCOMPLETE": "terms missing or terms_hash mismatch",
    "VERIFIER_MISMATCH": "verifier/version differs from task or agreement",
    "DEADLINE_NOT_MET": "delivery time exceeds requirement",
    "DATA_SCOPE": "data purpose not allowed",
    "DUPLICATE_EFFECT": "this negotiation already produced this effect",
    "STATE_INVALID": "negotiation state does not allow this action",
    "VERIFICATION_FAILED": "delivery did not pass the agreed verifier",
    "AGREEMENT_UNSIGNED": "agreement missing a valid party signature",
    "AMOUNT_MISMATCH": "amount differs from agreement",
    "COUNTER_NOT_IN_GRID": "counteroffer price outside the bounded grid",
    "ROUNDS_EXCEEDED": "maximum counteroffer rounds reached",
    "AUTH_EXPIRED": "authorization no longer valid",
    "AUTH_PARAMS_MISMATCH": "effective parameters differ from authorization",
}


class PolicyEngine:
    def __init__(self, store, identity, config: dict | None = None):
        self.store = store
        self.identity = identity
        self.config = copy.deepcopy(config or DEFAULT_POLICY)
        self.hash = hash_obj(self.config)

    # ------------------------------------------------------------ controls

    @property
    def paused(self) -> bool:
        return bool(self.store.get_kv("policy.paused", False))

    def set_paused(self, value: bool) -> None:
        self.store.set_kv("policy.paused", value)

    @property
    def budget(self) -> int:
        return int(self.config["budget_total"])

    def counter_prices(self, quote_price: int) -> list[int]:
        return [quote_price * p // 100 for p in self.config["counter_grid_percent"]]

    # ------------------------------------------------------------- results

    def _result(self, action: str, negotiation_id: str, code: str, rule: str, params: dict, state_ref: str) -> dict:
        res = {
            "result_id": random_id(),
            "allowed": code == "OK",
            "code": code,
            "rule": rule,
            "action": action,
            "negotiation_id": negotiation_id,
            "policy_version": self.config["version"],
            "policy_hash": self.hash,
            "params_hash": hash_obj(params),
            "max_amount": params.get("amount"),
            "state_ref": state_ref,
            "issued_at": iso(now()),
            "expires_at": iso(now() + int(self.config["authorization_ttl_seconds"])),
            "authorized_key": self.identity.key_id,
        }
        res = self.identity.sign_obj(res)
        self.store.put_record("policy_result", res["result_id"], res, negotiation_id)
        return res

    def check_authorization(self, auth: dict, params: dict) -> str:
        """Re-validation right before an effect. Returns a code."""
        if not auth.get("allowed") or not verify_obj(auth, self.identity.key_id):
            return auth.get("code", "STATE_INVALID")
        if auth["policy_hash"] != self.hash:
            return "AUTH_PARAMS_MISMATCH"
        if parse_iso(auth["expires_at"]) < now():
            return "AUTH_EXPIRED"
        if auth["params_hash"] != hash_obj(params):
            return "AUTH_PARAMS_MISMATCH"
        if self.paused and auth["action"] in ("ACCEPT_QUOTE", "FUND_ESCROW", "SEND_COUNTEROFFER"):
            return "PAUSED"
        return "OK"

    def _settlement_allowed(self, network: str, asset: str) -> bool:
        return {"network": network, "asset": asset} in self.config["allowed_settlement"]

    # ------------------------------------------------------------- actions

    def authorize_counteroffer(self, neg: dict, quote_msg: dict, price: int, round_: int) -> dict:
        params = {"quote_id": quote_msg["payload"]["quote_id"], "price": str(price), "round": round_}
        nid, ref = neg["id"], neg["state"]
        if self.paused:
            return self._result("SEND_COUNTEROFFER", nid, "PAUSED", "kill_switch", params, ref)
        if round_ > int(self.config["max_counter_rounds"]):
            return self._result("SEND_COUNTEROFFER", nid, "ROUNDS_EXCEEDED", "max_counter_rounds", params, ref)
        if price not in self.counter_prices(int(quote_msg["payload"]["terms"]["price"])):
            return self._result("SEND_COUNTEROFFER", nid, "COUNTER_NOT_IN_GRID", "counter_grid", params, ref)
        return self._result("SEND_COUNTEROFFER", nid, "OK", "counter_grid", params, ref)

    def authorize_accept(self, neg: dict, quote_msg: dict, advert: dict) -> dict:
        """Checks a quote before the buyer signs an agreement and reserves
        budget atomically (the reservation is the side effect of success)."""
        nid, ref, task = neg["id"], neg["state"], neg["data"]["task"]
        p = quote_msg["payload"]
        terms = p["terms"]
        price = int(terms["price"])
        params = {"quote_id": p["quote_id"], "terms_hash": p["terms_hash"], "amount": terms["price"]}

        def r(code, rule):
            return self._result("ACCEPT_QUOTE", nid, code, rule, params, ref)

        if self.paused:
            return r("PAUSED", "kill_switch")
        if neg["state"] != "NEGOTIATING":
            return r("STATE_INVALID", "state_machine")
        if quote_msg["sender_key_id"] != advert["agent_key_id"] or not verify_obj(quote_msg, advert["agent_key_id"]):
            return r("QUOTE_SIGNATURE_INVALID", "quote_signature")
        if terms["supplier_key_id"] != advert["agent_key_id"]:
            return r("QUOTE_SIGNATURE_INVALID", "quote_signature")
        if compute_terms_hash(terms) != p["terms_hash"] or terms["task_id"] != task["task_id"]:
            return r("QUOTE_INCOMPLETE", "quote_terms")
        if terms["dataset_sha256"] != task["dataset"]["sha256"]:
            return r("QUOTE_INCOMPLETE", "quote_terms")
        if parse_iso(terms["valid_until"]) < now():
            return r("QUOTE_EXPIRED", "quote_validity")
        if not self._settlement_allowed(terms["network"], terms["asset"]):
            return r("ASSET_NOT_ALLOWED", "allowed_settlement")
        if terms["network"] != task["network"] or terms["asset"] != task["asset"]:
            return r("ASSET_NOT_ALLOWED", "allowed_settlement")
        if terms["verifier"] != task["verifier"] or terms["verifier"] not in self.config["allowed_verifiers"]:
            return r("VERIFIER_MISMATCH", "allowed_verifiers")
        if terms["payee"] != advert["payee"]:
            return r("PAYEE_MISMATCH", "recipient")
        if terms["delivery_seconds"] > task["requirements"]["max_delivery_seconds"]:
            return r("DEADLINE_NOT_MET", "requirements")
        task_budget = min(int(task["budget"]), int(self.config["max_per_task"]))
        if price > task_budget:
            return r("BUDGET_EXCEEDED", "budget")
        if not self.store.reserve(nid, price, self.budget):
            return r("BUDGET_EXCEEDED", "budget_reservation")
        return r("OK", "all_rules")

    def authorize_fund(self, neg: dict, params: dict) -> dict:
        nid, ref, d = neg["id"], neg["state"], neg["data"]
        ag = d.get("agreement")

        def r(code, rule):
            return self._result("FUND_ESCROW", nid, code, rule, params, ref)

        if self.paused:
            return r("PAUSED", "kill_switch")
        if neg["state"] not in ("AGREED", "FUNDING_PENDING"):
            return r("STATE_INVALID", "state_machine")
        if self.store.get_effect(nid, "FUND_ESCROW"):
            return r("DUPLICATE_EFFECT", "idempotency")
        if not ag or not d.get("agreement_signatures", {}).get("supplier"):
            return r("AGREEMENT_UNSIGNED", "agreement")
        if params["amount"] != ag["price"]:
            return r("AMOUNT_MISMATCH", "agreement")
        if params["payee"] != ag["payee"]:
            return r("PAYEE_MISMATCH", "recipient")
        if not self._settlement_allowed(params["network"], params["asset"]):
            return r("ASSET_NOT_ALLOWED", "allowed_settlement")
        if (params["network"], params["asset"]) != (ag["network"], ag["asset"]):
            return r("ASSET_NOT_ALLOWED", "allowed_settlement")
        rows = self.store.q("SELECT amount, status FROM reservations WHERE negotiation_id=?", (nid,))
        if not rows or rows[0]["status"] != "active" or rows[0]["amount"] != int(ag["price"]):
            return r("BUDGET_EXCEEDED", "budget_reservation")
        return r("OK", "all_rules")

    def authorize_share_data(self, neg: dict, purpose: str, escrow_state: str | None) -> dict:
        params = {"purpose": purpose, "dataset_sha256": neg["data"]["task"]["dataset"]["sha256"]}
        if purpose not in self.config["data_purposes"]:
            return self._result("SHARE_DATA", neg["id"], "DATA_SCOPE", "data_purposes", params, neg["state"])
        if escrow_state != "FUNDED":
            return self._result("SHARE_DATA", neg["id"], "STATE_INVALID", "funded_before_data", params, neg["state"])
        return self._result("SHARE_DATA", neg["id"], "OK", "data_purposes", params, neg["state"])

    def authorize_release(self, neg: dict, params: dict, verification_ok: bool) -> dict:
        nid, ref, ag = neg["id"], neg["state"], neg["data"]["agreement"]

        def r(code, rule):
            return self._result("RELEASE_PAYMENT", nid, code, rule, params, ref)

        if neg["state"] != "VERIFIED":
            return r("STATE_INVALID", "state_machine")
        if not verification_ok:
            return r("VERIFICATION_FAILED", "verifier")
        if self.store.get_effect(nid, "RELEASE_PAYMENT"):
            return r("DUPLICATE_EFFECT", "idempotency")
        if params["amount"] != ag["price"]:
            return r("AMOUNT_MISMATCH", "agreement")
        if params["payee"] != ag["payee"]:
            return r("PAYEE_MISMATCH", "recipient")
        return r("OK", "all_rules")
