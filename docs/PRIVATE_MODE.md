# Private mode: Cripito over the Nym mixnet

## What changes and what does not

The Cripito protocol stays identical: outer envelopes, mailbox routes, signed adverts and encrypted
blobs. Only the path to the relay changes:

| | direct (`--transport direct`) | mixnet (`--transport mixnet`) |
|---|---|---|
| Content | end-to-end encrypted | end-to-end encrypted |
| Relay sees client IP | **yes** | no (traffic goes through the mixnet) |
| Relay sees client Nym address | n/a | no: anonymous sends, replies via SURBs (sender tag only) |
| Relay sees mailbox route, size class, its own timing | yes | yes |
| Protection against a global passive observer | none | depends on the Nym deployment (mixing, cover traffic); not claimed |
| Settlement privacy | none: public and pseudonymous on chain | same: the mixnet does not hide payments |
| If unavailable | errors | **fails explicitly; never downgrades to direct** |

Each agent talks to its own local `nym-client` through the websocket API (message format taken from
nym's `clients/native/websocket-requests/src/text.rs`). The relay runs a `RelayNymGateway` on its own
`nym-client` and answers each anonymous request through its reply SURBs.

Mixnet latency is seconds, not milliseconds. Use longer windows: for example `quote_window_seconds` of
30 or more, and keep the default 3 retransmissions.

## Status

- Tested against a `nym-client` test double that implements that message format
  (`tests/test_mixnet.py`). The tests cover a full negotiation and settlement through the "mixnet", an
  assertion that the relay endpoint never receives any agent's Nym address, the task, prices or the
  buyer's key, and an outage that must not downgrade.
- **Not yet run on the live Nym network.** The build environment could not reach it. Nym's client CLI
  and flags change between releases: check the commands below against the version you install.

## Running it (on a machine with internet access)

1. Get `nym-client` from the Nym releases page (https://github.com/nymtech/nym/releases) and start one
   per participant, each on its own port:
   ```bash
   ./nym-client init --id cripito-relay  && ./nym-client run --id cripito-relay  --port 1977
   ./nym-client init --id cripito-buyer  && ./nym-client run --id cripito-buyer  --port 1978
   ./nym-client init --id cripito-alpha  && ./nym-client run --id cripito-alpha  --port 1979
   ```
2. Start the relay as a mixnet service. It prints its Nym address:
   ```bash
   python -m cripito relay --nym-client ws://127.0.0.1:1977
   # [relay] mixnet service address (give to agents as --relay-nym): <RELAY_NYM_ADDRESS>
   ```
3. Start the agents in mixnet mode:
   ```bash
   python -m cripito ledger
   python -m cripito supplier --name alpha --price 90 --min-price 78 \
       --transport mixnet --nym-client ws://127.0.0.1:1979 --relay-nym <RELAY_NYM_ADDRESS>
   python -m cripito buyer \
       --transport mixnet --nym-client ws://127.0.0.1:1978 --relay-nym <RELAY_NYM_ADDRESS>
   ```
If a `nym-client` is down, the agent refuses to start, or reports `TRANSPORT_UNAVAILABLE` on the task
and waits. It never sends anything over direct HTTP instead.
