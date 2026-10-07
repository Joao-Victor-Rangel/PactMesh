"""Transport conformance suite: every backend must give the same semantics
for send / receive / acknowledge / status / adverts / blobs (spec section 5:
"adapters must pass the same conformance suite")."""

import threading

import pytest

from pactmesh.crypto import Identity, mailbox_route, open_envelope, random_id, seal
from pactmesh.protocol import build_advert
from pactmesh.transport import DirectTransport, MixnetTransport, TransportUnavailable
from pactmesh.transport.nym import RelayNymGateway
from pactmesh.util import now
from nym_double import MockMixnet


@pytest.fixture(params=["direct", "mixnet"])
def transport_factory(request, net):
    if request.param == "direct":
        yield lambda: DirectTransport([net.relay_url])
        return
    mix = MockMixnet()
    gw = RelayNymGateway(net.relay, mix.client().url)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    yield lambda: MixnetTransport(mix.client().url, gw.address, timeout=10)
    for c in list(mix.clients.values()):
        c.stop()


def _env(ident, route, body=b'{"x":1}'):
    return seal(body, ident.enc_public_b64, route, now() + 120)


def test_send_receive_ack_semantics(transport_factory):
    sender, receiver = transport_factory(), transport_factory()
    me = Identity.generate()
    secret = random_id()
    route = mailbox_route(secret)
    e1, e2 = _env(me, route, b'{"n":1}'), _env(me, route, b'{"n":2}')
    sender.send(e1)
    sender.send(e2)
    got = receiver.receive(secret)
    assert [e["id"] for _, e in got] == [e1["id"], e2["id"]]  # FIFO per mailbox
    assert open_envelope(got[0][1], me) == b'{"n":1}'
    # at-least-once: not acknowledged -> delivered again
    assert [e["id"] for _, e in receiver.receive(secret)] == [e1["id"], e2["id"]]
    relay_id = got[0][0]
    receiver.acknowledge(relay_id, secret, [e1["id"]])
    left = receiver.receive(secret)  # mixnet piggybacks the ack on this fetch
    left = left if len(left) == 1 else receiver.receive(secret)
    assert [e["id"] for _, e in left] == [e2["id"]]


def test_public_route_is_not_a_read_capability(transport_factory):
    t = transport_factory()
    me = Identity.generate()
    secret = random_id()
    route = mailbox_route(secret)
    t.send(_env(me, route))
    assert t.receive(route) == []  # the route is not the secret
    assert len(t.receive(secret)) == 1


def test_rejects_malformed_or_expired(transport_factory):
    t = transport_factory()
    me = Identity.generate()
    route = mailbox_route(random_id())
    good = _env(me, route)
    for bad in ({**good, "v": "pactmesh-transport/9"}, {**good, "size": 123}, {**good, "extra": 1}):
        with pytest.raises(TransportUnavailable):
            t.send(bad)
    # The relay cannot see header tampering (no key); the recipient's AEAD check does.
    t.send({**good, "nonce": _env(me, route)["nonce"]})
    expired = {**_env(me, route), "exp": now() - 10}
    with pytest.raises(TransportUnavailable):
        t.send(expired)


def test_adverts_and_blobs(transport_factory):
    t = transport_factory()
    ident = Identity.generate()
    adv = build_advert(ident, route=mailbox_route(random_id()), name="x", service="stats-report",
                       verifier={"name": "pactmesh.stats", "version": "1.0"},
                       networks=[{"network": "n", "asset": "a"}], payee="p", ttl=60, description="d")
    assert t.publish_advert(adv) >= 1
    assert any(a["agent_key_id"] == ident.key_id for a in t.fetch_adverts())
    forged = {**adv, "description": "changed after signing"}
    assert t.publish_advert(forged) == 0
    blob = bytes(range(256)) * 50
    bid = random_id()
    assert t.put_blob(bid, blob) >= 1
    assert t.get_blob(bid) == blob
    with pytest.raises(TransportUnavailable):
        t.get_blob(random_id())


def test_status_reports_mode_and_guarantees(transport_factory):
    t = transport_factory()
    st = t.status()
    assert st["mode"] in ("direct", "mixnet") and "guarantees" in st
    if st["mode"] == "direct":
        assert "NOT anonymous" in st["guarantees"]
