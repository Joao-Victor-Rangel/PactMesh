import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from pactmesh import util  # noqa: E402
from pactmesh.buyer import Buyer  # noqa: E402
from pactmesh.decision import make_engine  # noqa: E402
from pactmesh.httpbase import run_in_thread  # noqa: E402
from pactmesh.ledger import SimLedgerClient  # noqa: E402
from pactmesh.ledger.sim import SimLedger  # noqa: E402
from pactmesh.supplier import Supplier  # noqa: E402
from pactmesh.transport import DirectTransport  # noqa: E402
from pactmesh.transport.relay import Relay  # noqa: E402
from make_dataset import make  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_clock():
    util._offset = 0.0
    yield
    util._offset = 0.0


class Net:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.relay = Relay(observation_log=tmp / "relay_observed.jsonl")
        self.relay_srv = self.relay.app.serve("127.0.0.1", 0)
        run_in_thread(self.relay_srv)
        self.ledger = SimLedger(tmp / "ledger.sqlite")
        self.ledger_srv = self.ledger.app.serve("127.0.0.1", 0)
        run_in_thread(self.ledger_srv)
        self.relay_url = f"http://127.0.0.1:{self.relay_srv.server_address[1]}"
        self.ledger_url = f"http://127.0.0.1:{self.ledger_srv.server_address[1]}"
        self.agents = []

    def transport(self):
        return DirectTransport([self.relay_url])

    def supplier(self, name, price, min_price, delivery=60, description="", behavior="honest", fund=True):
        s = Supplier(self.tmp / name, name, self.transport(), SimLedgerClient(self.ledger_url), price=price,
                     min_price=min_price, delivery_seconds=delivery, description=description, behavior=behavior)
        if fund:
            s.ledger.faucet()
        self.agents.append(s)
        return s

    def buyer(self, engine="reference", name="buyer", **kw):
        b = Buyer(self.tmp / name, name, self.transport(), SimLedgerClient(self.ledger_url),
                  engine=make_engine(engine), **kw)
        b.ledger.faucet()
        self.agents.append(b)
        return b

    def run(self, until, max_steps=400, advance=0.0):
        for _ in range(max_steps):
            for a in self.agents:
                a.step()
            if until():
                return True
            if advance:
                util.advance_clock(advance)
        return False

    def close(self):
        for srv in (self.relay_srv, self.ledger_srv):
            try:
                srv.shutdown()
                srv.server_close()
            except OSError:
                pass


@pytest.fixture
def net(tmp_path):
    n = Net(tmp_path)
    yield n
    n.close()


@pytest.fixture
def dataset():
    return make()
