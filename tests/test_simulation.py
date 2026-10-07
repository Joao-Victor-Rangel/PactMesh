"""Market simulation: many agents, adversaries and chaos; every global invariant must hold."""

from pactmesh.simulation import Market


def test_small_market_holds_all_invariants(tmp_path):
    m = Market(seed=3, buyers=3, suppliers=8, tasks_per_buyer=5, buyer_budget_total=400, task_spacing=45,
               work_dir=tmp_path / "sim")
    try:
        r = m.run()
    finally:
        m.close()
    failed = [i for i in r["invariants"] if not i["ok"]]
    assert not failed, failed
    assert r["outcomes"].get("SETTLED", 0) > 0  # honest suppliers do get paid once buyers learn
    assert r["outcomes"].get("DISPUTED", 0) > 0 and r["outcomes"].get("EXPIRED", 0) > 0
    assert any(e["event"] == "chaos" for e in r["timeline"]) and r["replays_posted"] > 0
