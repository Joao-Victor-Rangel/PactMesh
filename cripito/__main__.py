"""Command line: run each participant as an independent process.

    python -m cripito relay    --port 8701
    python -m cripito ledger   --port 8702
    python -m cripito supplier --name alpha --price 90 --min-price 78
    python -m cripito buyer    --api-port 8700
    python -m cripito demo                 # orchestrates all of the above
    python -m cripito verify evidence.json
    python -m cripito eval                 # synthetic evaluation suite
    python -m cripito solana-anchor .cripito-demo/evidence.json --keypair devnet.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path

from .httpbase import run_in_thread

ROOT = Path(__file__).resolve().parents[1]


def _transport(args):
    from .transport import TransportUnavailable, make_transport

    t = make_transport(args.transport, args.relay, args.nym_client)
    if args.transport == "mixnet":
        try:
            t.fetch_adverts()
        except TransportUnavailable as e:
            sys.exit(f"[cripito] private mode requested but unavailable: {e}")
    return t


def cmd_relay(args):
    from .transport.relay import Relay

    relay = Relay(Path(args.observe) if args.observe else None)
    srv = relay.app.serve(args.host, args.port)
    print(f"[relay] direct-mode relay on http://{args.host}:{args.port} (sees only opaque envelopes)", flush=True)
    srv.serve_forever()


def cmd_ledger(args):
    from .ledger.sim import SimLedger

    led = SimLedger(args.db)
    srv = led.app.serve(args.host, args.port)
    print(f"[ledger] SIMULATED ledger on http://{args.host}:{args.port} (not a blockchain)", flush=True)
    srv.serve_forever()


def _ledger(args):
    """Settlement backend: the SIMULATED ledger or Solana (Devnet or cripito-localnet)."""
    if getattr(args, "chain", "sim") == "solana":
        from .ledger.solana import DEVNET_RPC, SolanaEscrowClient

        if not args.program_id:
            sys.exit("[cripito] --program-id is required with --chain solana (see docs/SOLANA.md)")
        return SolanaEscrowClient(args.rpc or DEVNET_RPC, args.program_id)
    from .ledger import SimLedgerClient

    return SimLedgerClient(args.ledger)


def _use_keypair(agent, args):
    if getattr(args, "keypair", None):
        from .ledger import Wallet

        agent.wallet = Wallet.from_solana_keypair(Path(args.keypair))
        agent.ledger.wallet = agent.wallet


def _ensure_funds(agent):
    from .ledger import LedgerError

    for _ in range(50):
        try:
            agent.ledger.ensure_funds()
            return
        except (LedgerError, OSError):
            time.sleep(0.2)


def cmd_supplier(args):
    from .supplier import Supplier

    s = Supplier(Path(args.home or f".cripito/{args.name}"), args.name, _transport(args), _ledger(args),
                 price=args.price, min_price=args.min_price, delivery_seconds=args.delivery,
                 description=args.description, behavior=args.behavior)
    _use_keypair(s, args)
    _ensure_funds(s)
    print(f"[{args.name}] supplier key {s.identity.key_id[:16]}… price {args.price} (min {args.min_price})", flush=True)
    s.run()


def cmd_buyer(args):
    from .api import build_api
    from .buyer import Buyer
    from .decision import make_engine
    policy = json.loads(Path(args.policy).read_text()) if args.policy else None
    home = Path(args.home)
    b = Buyer(home, args.name, _transport(args), _ledger(args),
              engine=make_engine(args.engine, args.model_url), policy=policy)
    _use_keypair(b, args)
    _ensure_funds(b)
    print(f"[buyer] settlement {b.ledger.network} ({'SIMULATED' if b.ledger.simulated else 'on-chain'}), "
          f"payer {b.ledger.address}", flush=True)
    token_file = home / "admin_token"
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(24))
    token = token_file.read_text().strip()
    dataset = Path(args.dataset).read_bytes() if args.dataset else None
    srv = build_api(b, token, dataset).serve(args.host, args.api_port)
    run_in_thread(srv)
    print(f"[buyer] dashboard http://{args.host}:{args.api_port}/#token={token}", flush=True)
    b.run()


def cmd_verify(args):
    from .audit import verify_package
    pkg = json.loads(Path(args.package).read_text())
    res = verify_package(pkg, _ledger(args) if (args.ledger or args.chain == "solana") else None)
    for c in res["checks"]:
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']} {c['detail']}")
    print("VALID" if res["ok"] else "INVALID")
    sys.exit(0 if res["ok"] else 1)


def cmd_eval(args):
    sys.path.insert(0, str(ROOT))
    from evaluation.run_eval import main

    main(args.out)


def cmd_solana_anchor(args):
    from nacl.signing import SigningKey

    from .ledger.solana import DEVNET_RPC, SolanaMemoAnchor

    secret = bytes(json.loads(Path(args.keypair).read_text()))  # solana-keygen JSON (64 bytes)
    batch = json.loads(Path(args.package).read_text())["receipt"]["evidence_batch"]
    anchor = SolanaMemoAnchor(SigningKey(secret[:32]), args.rpc or DEVNET_RPC)
    print(f"[solana] payer {anchor.address} anchoring root {batch['root'][:16]}... (Devnet, memo)")
    sig = anchor.anchor(batch["batch_id"], batch["root"], batch["version"])
    print(f"[solana] signature {sig}\nhttps://explorer.solana.com/tx/{sig}?cluster=devnet")


def cmd_solana_keygen(args):
    from nacl.signing import SigningKey

    from .ledger.solana import DEVNET_RPC, SolanaMemoAnchor

    path = Path(args.out)
    if path.exists():
        key = SigningKey(bytes(json.loads(path.read_text()))[:32])
        print(f"[solana] using existing {path}")
    else:
        key = SigningKey.generate()
        secret = list(bytes(key) + key.verify_key.encode())  # solana-keygen JSON format (64 bytes)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(secret, f)
        print(f"[solana] new Devnet-only keypair written to {path} (never commit it; no real funds)")
    anchor = SolanaMemoAnchor(key, args.rpc or DEVNET_RPC)
    print(f"[solana] address {anchor.address}")
    if args.airdrop:
        try:
            sig = anchor._rpc("requestAirdrop", [anchor.address, 1_000_000_000])
            print(f"[solana] airdrop of 1 SOL (Devnet) requested: {sig}")
        except Exception as e:  # rate-limited or blocked network
            print(f"[solana] airdrop failed ({e}); use https://faucet.solana.com with the address above")


def cmd_demo(args):
    from .demo import run_demo

    run_demo(keep=args.keep, engine=args.engine, chain=args.chain)


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="cripito")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def chain(p):
        p.add_argument("--chain", choices=["sim", "solana"], default="sim")
        p.add_argument("--rpc", help="Solana RPC URL (default Devnet; use cripito-localnet for offline runs)")
        p.add_argument("--program-id", help="deployed cripito-escrow program id")

    def net(p):
        p.add_argument("--relay", action="append", default=None, help="relay URL (repeat for mirrors)")
        p.add_argument("--ledger", default="http://127.0.0.1:8702", help="SIMULATED ledger URL (--chain sim)")
        chain(p)
        p.add_argument("--keypair", help="solana-keygen JSON to use as this agent's payment key")
        p.add_argument("--transport", choices=["direct", "mixnet"], default="direct")
        p.add_argument("--nym-client", default=None)

    p = sub.add_parser("relay"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8701)
    p.add_argument("--observe", help="write what the relay observes (headers only) to this file"); p.set_defaults(fn=cmd_relay)
    p = sub.add_parser("ledger"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8702)
    p.add_argument("--db", default=".cripito/ledger.sqlite"); p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("supplier"); net(p); p.add_argument("--name", required=True); p.add_argument("--home")
    p.add_argument("--price", type=int, required=True); p.add_argument("--min-price", type=int, required=True)
    p.add_argument("--delivery", type=int, default=60); p.add_argument("--description", default="")
    p.add_argument("--behavior", choices=["honest", "bad_format", "wrong_values", "silent"], default="honest")
    p.set_defaults(fn=cmd_supplier)
    p = sub.add_parser("buyer"); net(p); p.add_argument("--name", default="buyer"); p.add_argument("--home", default=".cripito/buyer")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--api-port", type=int, default=8700)
    p.add_argument("--engine", choices=["reference", "simulated-llm", "http", "http+fallback"], default="reference")
    p.add_argument("--model-url"); p.add_argument("--policy", help="policy JSON file")
    p.add_argument("--dataset", default=str(ROOT / "examples" / "dataset.csv")); p.set_defaults(fn=cmd_buyer)
    p = sub.add_parser("verify"); p.add_argument("package"); p.add_argument("--ledger"); chain(p); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("eval"); p.add_argument("--out", default=str(ROOT / "evaluation" / "results.json")); p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("solana-keygen", help="create a Devnet-only keypair (and optionally request an airdrop)")
    p.add_argument("--out", default="devnet.json"); p.add_argument("--airdrop", action="store_true"); p.add_argument("--rpc")
    p.set_defaults(fn=cmd_solana_keygen)
    p = sub.add_parser("solana-anchor", help="anchor an evidence batch root on Solana Devnet (memo)")
    p.add_argument("package"); p.add_argument("--keypair", required=True); p.add_argument("--rpc")
    p.set_defaults(fn=cmd_solana_anchor)
    p = sub.add_parser("demo"); p.add_argument("--keep", action="store_true", help="keep processes running for the dashboard")
    p.add_argument("--engine", choices=["reference", "simulated-llm"], default="simulated-llm")
    p.add_argument("--chain", choices=["sim", "localnet"], default="sim",
                   help="sim: SIMULATED ledger; localnet: Rust escrow program via cripito-localnet"); p.set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    if hasattr(args, "relay") and not args.relay:
        args.relay = ["http://127.0.0.1:8701"]
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    args.fn(args)


if __name__ == "__main__":
    main()
