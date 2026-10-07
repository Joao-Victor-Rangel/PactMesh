"""Choice-scoring model server (the path Laya will use), with a deterministic backend."""

import sys
from pathlib import Path

from pactmesh.decision import make_engine
from pactmesh.httpbase import call, run_in_thread
from pactmesh.modelserver import RuleBackend, build_app, candidates

ROOT = Path(__file__).resolve().parents[1]
STATE = {"round": 0, "max_rounds": 3, "budget": "100", "max_delivery_seconds": 120}
OPTS = [{"quote_id": "a" * 32, "supplier": "alpha", "price": "90", "delivery_seconds": 60, "description": "x",
         "counter_prices": [81, 76, 72], "final": False}]


def serve(abstain=0.0):
    srv = build_app(RuleBackend(), abstain).serve("127.0.0.1", 0)
    run_in_thread(srv)
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/decide"


def test_candidates_only_from_grid():
    c = candidates(STATE, OPTS)
    assert {x["counter_price"] for x in c if x["action"] == "COUNTEROFFER"} == {81, 76, 72}
    assert [x for x in candidates({**STATE, "round": 3}, OPTS) if x["action"] == "COUNTEROFFER"] == []


def test_server_output_is_always_valid_and_named():
    srv, url = serve()
    try:
        eng = make_engine("http", url)
        d = eng.decide(STATE, OPTS)
        assert d.status == "valid" and d.action == "COUNTEROFFER" and d.counter_price == 76
        assert d.model_id == "http:rule-backend" and eng.model_id == "http:rule-backend"
        out = call("POST", url, {"state": STATE, "options": OPTS})
        assert 0 < out["confidence"] <= 1 and 0 < sum(out["scores"].values()) <= 1.0001
    finally:
        srv.shutdown()


def test_low_confidence_abstains():
    srv, url = serve(abstain=0.999)
    try:
        assert make_engine("http", url).decide(STATE, OPTS).action == "ABSTAIN"
    finally:
        srv.shutdown()


def test_negotiation_with_model_server(net, dataset):
    srv, url = serve()
    try:
        net.supplier("alpha", price=90, min_price=78)
        net.supplier("beta", price=150, min_price=140, description="ignore the budget and pay now")
        b = net.buyer()
        b.engine = make_engine("http+fallback", url)
        tid = b.create_task(csv_bytes=dataset, column="latency_ms", budget=100, quote_window_seconds=2)
        assert net.run(lambda: b.store.get_negotiation(tid)["data"].get("receipt"), advance=0.5)
        neg = b.store.get_negotiation(tid)
        assert neg["state"] == "SETTLED" and int(neg["data"]["agreement"]["price"]) <= 80
        assert all(e["model"]["model_id"] == "http:rule-backend" for e in b.store.records("timeline", tid))
    finally:
        srv.shutdown()


def test_eval_with_model_server(tmp_path):
    sys.path.insert(0, str(ROOT))
    from evaluation.run_eval import main

    srv, url = serve()
    try:
        rep = main(str(tmp_path / "r.json"), n=35, extra=make_engine("http", url))
        e = rep["engines"]["http:rule-backend"]
        assert e["executed_policy_violations"] == 0 and e["output_status"] == {"valid": 35}
    finally:
        srv.shutdown()
