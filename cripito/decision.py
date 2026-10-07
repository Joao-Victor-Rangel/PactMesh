"""Local decision engines ("Jev-style" typed decisions).

Contract: input is a structured negotiation state plus a closed list of
options; output is one of ACCEPT, COUNTEROFFER, REQUEST_INFO, REJECT,
ABSTAIN with an option reference, scores, model identity, latency and a
validity status. Engines never sign, never see wallet keys and cannot
invent executable parameters: counteroffer prices must come from the
grid supplied in the options, and the policy engine re-checks everything.

Engines provided:

* ``ReferenceEngine`` - deterministic, transparent weights. Baseline.
* ``SimulatedLLMEngine`` - a *test double* that behaves like a gullible
  language model: it follows instructions embedded in supplier text. Used
  to demonstrate that policy blocks prompt-injected recommendations.
* ``HttpModelEngine`` - adapter for a local model server (e.g. a Laya
  checkpoint behind a small HTTP shim). Output is strictly validated;
  unavailability yields ABSTAIN or the declared deterministic fallback.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

ACTIONS = ("ACCEPT", "COUNTEROFFER", "REQUEST_INFO", "REJECT", "ABSTAIN")


@dataclass
class Decision:
    action: str
    quote_id: str | None = None
    counter_price: int | None = None
    scores: dict[str, str] = field(default_factory=dict)  # decimal strings, no floats
    model_id: str = ""
    model_revision: str = ""
    latency_ms: int = 0
    status: str = "valid"  # valid | invalid_output | unavailable | fallback
    generated_rationale: str = ""  # model text: NOT evidence of internal reasoning

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["counter_price"] is not None:
            d["counter_price"] = str(d["counter_price"])
        return d


def _fmt(x: float) -> str:
    return f"{x:.4f}"


class ReferenceEngine:
    model_id = "cripito-reference-rules"
    model_revision = "1"

    def __init__(self, w_price: float = 0.7, w_time: float = 0.3, target_percent: int = 80):
        self.w_price, self.w_time, self.target_percent = w_price, w_time, target_percent

    def decide(self, state: dict, options: list[dict]) -> Decision:
        t0 = time.perf_counter()
        d = self._decide(state, options)
        d.model_id, d.model_revision = self.model_id, self.model_revision
        d.latency_ms = int((time.perf_counter() - t0) * 1000)
        return d

    def _decide(self, state: dict, options: list[dict]) -> Decision:
        budget = int(state["budget"])
        rounds_left = state["round"] < state["max_rounds"]
        if not options:
            return Decision("REJECT")
        max_t = state["max_delivery_seconds"]
        scores = {
            o["quote_id"]: self.w_price * int(o["price"]) / budget + self.w_time * o["delivery_seconds"] / max_t
            for o in options
        }
        fmt_scores = {k: _fmt(v) for k, v in scores.items()}
        within = [o for o in options if int(o["price"]) <= budget]
        target = budget * self.target_percent // 100
        if not within:
            cheapest = min(options, key=lambda o: int(o["price"]))
            grid = [p for p in cheapest["counter_prices"] if p <= budget]
            if rounds_left and grid:
                return Decision("COUNTEROFFER", cheapest["quote_id"], max(grid), fmt_scores)
            return Decision("REJECT", scores=fmt_scores)
        best = min(within, key=lambda o: scores[o["quote_id"]])
        if int(best["price"]) <= target or not rounds_left or best.get("final"):
            return Decision("ACCEPT", best["quote_id"], scores=fmt_scores)
        grid = sorted(best["counter_prices"], reverse=True)
        price = next((p for p in grid if p <= target), grid[-1])
        return Decision("COUNTEROFFER", best["quote_id"], price, fmt_scores)


INJECTION_BAIT = re.compile(r"ignore|pay now|override|urgent|disregard", re.I)


class SimulatedLLMEngine(ReferenceEngine):
    """Deliberately gullible test double; stands in for an unspecialized LM."""

    model_id = "simulated-llm-gullible"
    model_revision = "demo-1"

    def _decide(self, state: dict, options: list[dict]) -> Decision:
        for o in options:
            if INJECTION_BAIT.search(o.get("description", "")):
                return Decision(
                    "ACCEPT",
                    o["quote_id"],
                    scores={o["quote_id"]: "0.9700"},
                    generated_rationale=f"Supplier text says: {o['description'][:120]!r}. Following it.",
                )
        d = super()._decide(state, options)
        d.generated_rationale = "Chose the option with the best price/time trade-off."
        return d


class HttpModelEngine:
    """Calls a local model server: POST {state, options, allowed_actions}
    -> {action, quote_id?, counter_price?, scores?, rationale?}."""

    def __init__(self, url: str, model_id: str, model_revision: str, timeout: float = 5.0):
        self.url, self.model_id, self.model_revision, self.timeout = url, model_id, model_revision, timeout

    def decide(self, state: dict, options: list[dict]) -> Decision:
        t0 = time.perf_counter()
        body = json.dumps({"state": state, "options": options, "allowed_actions": list(ACTIONS)}).encode()
        try:
            req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError):
            return Decision("ABSTAIN", model_id=self.model_id, model_revision=self.model_revision,
                            latency_ms=int((time.perf_counter() - t0) * 1000), status="unavailable")
        d = Decision("ABSTAIN", model_id=self.model_id, model_revision=self.model_revision,
                     latency_ms=int((time.perf_counter() - t0) * 1000), status="invalid_output")
        ids = {o["quote_id"]: o for o in options}
        action = out.get("action") if isinstance(out, dict) else None
        if action not in ACTIONS:
            return d
        qid = out.get("quote_id")
        if action in ("ACCEPT", "COUNTEROFFER") and qid not in ids:
            return d
        price = out.get("counter_price")
        if action == "COUNTEROFFER":
            try:
                price = int(price)
            except (TypeError, ValueError):
                return d
            if price not in ids[qid]["counter_prices"]:
                return d
        scores = out.get("scores") or {}
        d.action, d.quote_id, d.counter_price, d.status = action, qid, price, "valid"
        d.scores = {str(k): _fmt(float(v)) for k, v in scores.items() if k in ids} if isinstance(scores, dict) else {}
        d.generated_rationale = str(out.get("rationale", ""))[:500]
        return d


class FallbackEngine:
    """Primary engine with a *declared* deterministic fallback."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.model_id = f"{primary.model_id}|fallback:{fallback.model_id}"

    def decide(self, state: dict, options: list[dict]) -> Decision:
        d = self.primary.decide(state, options)
        if d.status in ("unavailable", "invalid_output"):
            fb = self.fallback.decide(state, options)
            fb.status = "fallback"
            return fb
        return d


def make_engine(name: str, model_url: str | None = None):
    if name == "reference":
        return ReferenceEngine()
    if name == "simulated-llm":
        return SimulatedLLMEngine()
    if name in ("http", "http+fallback"):
        if not model_url:
            raise ValueError("--model-url is required for the http engine")
        eng = HttpModelEngine(model_url, "local-http-model", "unpinned")
        return FallbackEngine(eng, ReferenceEngine()) if name == "http+fallback" else eng
    raise ValueError(f"unknown decision engine {name!r}")
