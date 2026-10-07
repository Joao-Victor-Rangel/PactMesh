"""Knowing a mailbox's public deposit route must not let anyone read or delete its messages."""

from pactmesh.crypto import Identity, seal
from pactmesh.httpbase import HttpError, call
from pactmesh.util import now


def test_route_holder_cannot_read_or_delete(net):
    s = net.supplier("alpha", price=80, min_price=80)
    route = s.advert()["route"]  # public: published in the signed advert
    env = seal(b'{"hello":"world"}', s.identity.enc_public_b64, route, now() + 120)
    call("POST", f"{net.relay_url}/mailbox/{route}", env)

    # Attacker knows the route only.
    for method, path, body in (("GET", f"/mailbox/{route}", None),
                               ("POST", f"/mailbox/{route}/ack", [env["id"]]),
                               ("POST", "/mailbox/fetch", {"secret": route}),
                               ("POST", "/mailbox/ack", {"secret": route, "ids": [env["id"]]})):
        try:
            out = call(method, net.relay_url + path, body)
        except HttpError:
            continue
        assert env["id"] not in str(out), f"{method} {path} leaked the envelope"

    # The owner still receives it: nothing was deleted by the attacker.
    got = s.transport.receive(s.mailboxes()[0])
    assert [e["id"] for _, e in got] == [env["id"]]


def test_mailbox_route_is_derived_from_secret(net):
    from pactmesh.crypto import mailbox_route

    s = net.supplier("alpha", price=80, min_price=80)
    secret = s.mailboxes()[0]
    assert s.advert()["route"] == mailbox_route(secret) != secret
    assert Identity  # keep import used
