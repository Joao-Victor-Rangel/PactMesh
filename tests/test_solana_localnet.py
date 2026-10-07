"""End to end on cripito-localnet: Python agents + the real Rust escrow processor.

Build first:  cargo build --manifest-path contracts/escrow/Cargo.toml --features localnet --bin cripito-localnet
(skipped automatically when the binary is missing).
"""

import socket
import subprocess
import time
from pathlib import Path

import pytest

from cripito.audit import verify_package
from cripito.buyer import Buyer
from cripito.decision import make_engine
from cripito.ledger.solana import SolanaEscrowClient, b58encode
from cripito.policy import DEFAULT_POLICY
from cripito.supplier import Supplier

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "contracts" / "escrow" / "target" / "debug" / "cripito-localnet"
PROGRAM_ID = b58encode(bytes([0x5C]) * 32)
POLICY = {**DEFAULT_POLICY, "allowed_settlement": [{"network": "solana-devnet", "asset": "SOL"}]}

pytestmark = pytest.mark.skipif(not BIN.exists(), reason="cripito-localnet not built")


@pytest.fixture
def rpc():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([str(BIN), "--port", str(port), "--program-id", PROGRAM_ID],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    probe = SolanaEscrowClient(url, PROGRAM_ID)
    for _ in range(50):
        if probe.health():
            break
        time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait()


def agents(net, rpc, supplier_behavior="honest"):
    def client():
        return SolanaEscrowClient(rpc, PROGRAM_ID)

    s = Supplier(net.tmp / "alpha", "alpha", net.transport(), client(), price=90, min_price=78,
                 delivery_seconds=60, description="exact stats", behavior=supplier_behavior)
    b = Buyer(net.tmp / "buyer", "buyer", net.transport(), client(), engine=make_engine("reference"), policy=POLICY)
    for a in (s, b):
        a.ledger.ensure_funds()
        net.agents.append(a)
    return s, b


def finished(b, tid):
    n = b.store.get_negotiation(tid)
    return n["state"] in ("SETTLED", "DISPUTED", "CANCELLED", "EXPIRED") and (
        n["state"] != "SETTLED" or n["data"].get("receipt"))


def run_real_time(net, cond, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        for a in net.agents:
            a.step()
        if cond():
            return True
        time.sleep(0.15)
    return False


def test_full_flow_on_rust_escrow(net, dataset, rpc):
    s, b = agents(net, rpc)
    payee_before = int(s.ledger.balance()["SOL"])
    tid = b.create_task(csv_bytes=dataset, column="latency_ms", budget=100, quote_window_seconds=1)
    assert run_real_time(net, lambda: finished(b, tid)), b.store.get_negotiation(tid)["data"].get("last_note")
    neg = b.store.get_negotiation(tid)
    assert neg["state"] == "SETTLED"
    esc = b.ledger.get_escrow(neg["data"]["escrow_id"])
    assert esc["state"] == "RELEASED" and esc["payee"] == s.ledger.address
    price = int(neg["data"]["agreement"]["price"])
    # supplier received exactly the agreed lamports (minus its own anchoring fees, if it anchored already)
    gained = int(s.ledger.balance()["SOL"]) - payee_before
    assert gained in (price, price - 5000, price - 10000)
    res = verify_package(b.evidence_package(tid), b.ledger)
    assert res["ok"], res
    assert any(c["check"] == "batch_anchored" and c["ok"] for c in res["checks"])


def test_bad_delivery_disputed_on_chain(net, dataset, rpc):
    s, b = agents(net, rpc, supplier_behavior="bad_format")
    tid = b.create_task(csv_bytes=dataset, column="latency_ms", budget=100, quote_window_seconds=1)
    assert run_real_time(net, lambda: finished(b, tid))
    neg = b.store.get_negotiation(tid)
    assert neg["state"] == "DISPUTED"
    assert b.ledger.get_escrow(neg["data"]["escrow_id"])["state"] == "DISPUTED"


def test_program_rejects_double_release_and_wrong_authority(net, rpc):
    from cripito.ledger import Wallet

    payer = SolanaEscrowClient(rpc, PROGRAM_ID, Wallet.load_or_create(net.tmp / "p.json"))
    payee = SolanaEscrowClient(rpc, PROGRAM_ID, Wallet.load_or_create(net.tmp / "q.json"))
    payer.ensure_funds()
    payee.ensure_funds()
    ah = "ab" * 32
    ag = {"payee": payee.address, "asset": "SOL", "price": "5000000"}
    assert payer.create_escrow(ag, ah, int(time.time()) + 3600)["ok"]
    eid = payer.escrow_id_for(ah)
    # resending the identical signed tx is idempotent; a different create for the same agreement is refused
    assert payer.create_escrow(ag, ah, int(time.time()) + 3601)["err"] == "ESCROW_EXISTS"
    assert payee.fund_escrow(eid, "5000000", "SOL")["err"] == "WRONG_AUTHORITY"
    assert payer.fund_escrow(eid, "4999999", "SOL")["err"] == "AMOUNT_OR_MINT_MISMATCH"
    assert payer.fund_escrow(eid, "5000000", "SOL")["ok"]
    assert payer.refund(eid)["err"] == "DEADLINE_NOT_REACHED"
    assert payer.release(eid, payer.address)["err"] == "PAYEE_MISMATCH"
    before = int(payee.balance()["SOL"])
    assert payer.release(eid, payee.address)["ok"]
    assert int(payee.balance()["SOL"]) == before + 5_000_000
    assert payer.release(eid, payee.address)["ok"]  # identical resend: deduplicated, no second payment
    assert int(payee.balance()["SOL"]) == before + 5_000_000
    time.sleep(0.5)  # new blockhash -> a genuinely new release transaction
    assert payer.release(eid, payee.address)["err"] == "ESCROW_TERMINAL"
    assert int(payee.balance()["SOL"]) == before + 5_000_000
