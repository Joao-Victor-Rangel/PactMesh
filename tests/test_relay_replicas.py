"""Two independent relays: losing one mid-negotiation must not lose the contract."""

from pactmesh.buyer import Buyer
from pactmesh.decision import make_engine
from pactmesh.httpbase import run_in_thread
from pactmesh.ledger import SimLedgerClient
from pactmesh.supplier import Supplier
from pactmesh.transport import DirectTransport
from pactmesh.transport.relay import Relay


def test_contract_survives_losing_a_relay_replica(net, dataset):
    relay2 = Relay()
    srv2 = relay2.app.serve("127.0.0.1", 0)
    run_in_thread(srv2)
    urls = [net.relay_url, f"http://127.0.0.1:{srv2.server_address[1]}"]

    def t():
        return DirectTransport(urls)

    s = Supplier(net.tmp / "alpha", "alpha", t(), SimLedgerClient(net.ledger_url), price=80, min_price=80,
                 delivery_seconds=60)
    b = Buyer(net.tmp / "buyer", "buyer", t(), SimLedgerClient(net.ledger_url), engine=make_engine("reference"))
    net.agents += [s, b]
    for a in (s, b):
        a.ledger.faucet()
    tid = b.create_task(csv_bytes=dataset, column="latency_ms", budget=100, quote_window_seconds=2)
    assert net.run(lambda: b.store.get_negotiation(tid)["state"] == "NEGOTIATING", advance=0.3)
    # Both replicas hold the same messages so far.
    assert relay2.mailboxes and net.relay.adverts and relay2.adverts

    net.relay_srv.shutdown()          # replica 1 goes away mid-negotiation
    net.relay_srv.server_close()
    assert net.run(lambda: b.store.get_negotiation(tid)["data"].get("receipt"), advance=0.5)
    assert b.store.get_negotiation(tid)["state"] == "SETTLED"
    # Replicated delivery never produced duplicate processing.
    assert b.store.q("SELECT COUNT(*) c FROM effects WHERE negotiation_id=? AND action='RELEASE_PAYMENT'",
                     (tid,))[0]["c"] == 1
    srv2.shutdown()
