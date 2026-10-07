import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_bench_measures_every_stage_and_cost(tmp_path):
    from evaluation.bench import STAGES, run

    r = run(2, "sim", str(tmp_path / "b.json"))
    assert r["settled"] == 2 and r["simulated_settlement"]
    for name, _, _ in STAGES:
        assert r["latency"][name]["n"] == 2 and r["latency"][name]["p50_ms"] >= 0
    c = r["cost_per_contract"]
    assert c["buyer_fees_and_rent_SIM-SOL"] == 4 * 5000  # create, fund, release, anchor
    assert c["relay_envelope_bytes"] > 0 and c["relay_blob_bytes"] > 0
