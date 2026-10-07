"""Latency and cost per contract, measured end to end (spec section 14).

Runs N complete contracts (discovery -> negotiation -> escrow -> delivery ->
verification -> settlement -> receipt) with real HTTP relay and ledger
servers on localhost, and reports p50/p95 per stage plus measured costs.

    python -m pactmesh bench --n 10                  # SIMULATED ledger
    python -m pactmesh bench --n 10 --chain localnet # Rust escrow via pactmesh-localnet

All agents run in one Python process here, so the numbers measure the
protocol, cryptography, storage and settlement path, not network latency.
Mixnet latency must be measured on the real Nym network; it is not
estimated here. Report these numbers with the hardware line they print.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from pactmesh.buyer import Buyer  # noqa: E402
from pactmesh.decision import make_engine  # noqa: E402
from pactmesh.httpbase import run_in_thread  # noqa: E402
from pactmesh.ledger import SimLedgerClient  # noqa: E402
from pactmesh.ledger.sim import SimLedger  # noqa: E402
from pactmesh.policy import DEFAULT_POLICY  # noqa: E402
from pactmesh.supplier import Supplier  # noqa: E402
from pactmesh.transport import DirectTransport  # noqa: E402
from pactmesh.transport.relay import Relay  # noqa: E402

STAGES = [
    ("discovery", "CREATED", "QUOTING"),
    ("quotes", "QUOTING", "NEGOTIATING"),
    ("negotiation", "NEGOTIATING", "AGREED"),
    ("funding", "AGREED", "FUNDED"),
    ("delivery", "FUNDED", "DELIVERED"),
    ("verification", "DELIVERED", "VERIFIED"),
    ("settlement", "VERIFIED", "SETTLED"),
    ("total", "CREATED", "SETTLED"),
]
LOCALNET_BIN = ROOT / "contracts" / "escrow" / "target" / "debug" / "pactmesh-localnet"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(n: int = 10, chain: str = "sim", out: str | None = None) -> dict:
    from make_dataset import make

    dataset = make()
    tmp = Path(tempfile.mkdtemp(prefix="pactmesh-bench-"))
    obs = tmp / "relay.jsonl"
    relay = Relay(observation_log=obs)
    rsrv = relay.app.serve("127.0.0.1", 0)
    run_in_thread(rsrv)
    relay_url = f"http://127.0.0.1:{rsrv.server_address[1]}"
    proc = None
    if chain == "localnet":
        from pactmesh.ledger.solana import SolanaEscrowClient, b58encode

        if not LOCALNET_BIN.exists():
            raise SystemExit("build pactmesh-localnet first (see docs/SOLANA.md)")
        port, pid = free_port(), b58encode(bytes([0x5C]) * 32)
        proc = subprocess.Popen([str(LOCALNET_BIN), "--port", str(port), "--program-id", pid],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)

        def ledger():
            return SolanaEscrowClient(f"http://127.0.0.1:{port}", pid)

        policy = {**DEFAULT_POLICY, "budget_total": str(100 * n),
                  "allowed_settlement": [{"network": "solana-devnet", "asset": "SOL"}]}
        fee_asset = "SOL"
    else:
        led = SimLedger(tmp / "ledger.sqlite")
        lsrv = led.app.serve("127.0.0.1", 0)
        run_in_thread(lsrv)
        url = f"http://127.0.0.1:{lsrv.server_address[1]}"

        def ledger():
            return SimLedgerClient(url)

        policy = {**DEFAULT_POLICY, "budget_total": str(100 * n)}
        fee_asset = "SIM-SOL"

    try:
        sup = Supplier(tmp / "alpha", "alpha", DirectTransport([relay_url]), ledger(), price=90, min_price=78,
                       delivery_seconds=60, description="exact stats")
        buyer = Buyer(tmp / "buyer", "buyer", DirectTransport([relay_url]), ledger(),
                      engine=make_engine("reference"), policy=policy)
        agents = [sup, buyer]
        for a in agents:
            a.ledger.ensure_funds()
        bal0 = {a.name: dict(a.ledger.balance()) for a in agents}

        results = []
        for _ in range(n):
            tid = buyer.create_task(csv_bytes=dataset, column="latency_ms", budget=100, quote_window_seconds=5)
            deadline = time.time() + 60
            while time.time() < deadline:
                for a in agents:
                    a.step()
                neg = buyer.store.get_negotiation(tid)
                if neg["data"].get("receipt") or neg["state"] in ("CANCELLED", "EXPIRED", "DISPUTED"):
                    break
                time.sleep(0.02)
            results.append(buyer.store.get_negotiation(tid))
        # let the supplier process the last receipts (its anchoring fees count too)
        for _ in range(40):
            for a in agents:
                a.step()
            time.sleep(0.02)
        bal1 = {a.name: dict(a.ledger.balance()) for a in agents}

        settled = [r for r in results if r["state"] == "SETTLED"]
        stages = {}
        for name, a, b in STAGES:
            xs = [r["data"]["timings_ms"][b] - r["data"]["timings_ms"][a] for r in settled
                  if a in r["data"]["timings_ms"] and b in r["data"]["timings_ms"]]
            stages[name] = {"p50_ms": pct(xs, 50), "p95_ms": pct(xs, 95), "n": len(xs)}

        observed = [json.loads(line) for line in obs.read_text(encoding="utf-8").splitlines()] if obs.exists() else []
        env_bytes = sum(o.get("size", 0) for o in observed if o["op"] == "post")
        blob_bytes = sum(o.get("bytes", 0) for o in observed if o["op"] == "blob")
        paid = sum(int(r["data"]["agreement"]["price"]) for r in settled)
        buyer_spent = int(bal0["buyer"][fee_asset]) - int(bal1["buyer"][fee_asset])
        supplier_fees = (int(bal0["alpha"][fee_asset]) + (paid if fee_asset == "SOL" else 0)
                         - int(bal1["alpha"][fee_asset]))
        if fee_asset == "SOL":  # service price is paid in the same asset: separate it from fees and rent
            buyer_fees_and_rent = buyer_spent - paid
        else:
            buyer_fees_and_rent = buyer_spent
        vault_bytes = sum(f.stat().st_size for f in (tmp / "buyer").glob("vault.sqlite*"))
        report = {
            "chain": chain if chain == "sim" else "pactmesh-localnet (Rust escrow, host emulator)",
            "simulated_settlement": chain == "sim",
            "hardware": f"{platform.machine()} · {os.cpu_count()} CPUs · {platform.system()} {platform.release()} · "
                        f"Python {platform.python_version()}",
            "contracts": n, "settled": len(settled),
            "states": {s: sum(1 for r in results if r["state"] == s) for s in {r["state"] for r in results}},
            "latency": stages,
            "note_latency": "quotes stage waits for all suppliers; settlement waits for finality "
                            "(SIMULATED: 8 slots x 0.4 s; localnet: 4 slots)",
            "cost_per_contract": {
                "service_price_avg": round(paid / max(len(settled), 1), 2),
                f"buyer_fees_and_rent_{fee_asset}": round(buyer_fees_and_rent / max(len(settled), 1), 1),
                f"supplier_fees_{fee_asset}": round(supplier_fees / max(len(settled), 1), 1),
                "relay_envelope_bytes": round(env_bytes / max(len(settled), 1)),
                "relay_blob_bytes": round(blob_bytes / max(len(settled), 1)),
                "relay_operations": round(len(observed) / max(len(settled), 1), 1),
                "buyer_vault_bytes": round(vault_bytes / max(len(settled), 1)),
            },
        }
    finally:
        if proc:
            proc.terminate()
        rsrv.shutdown()
    text = json.dumps(report, indent=2)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return report


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
