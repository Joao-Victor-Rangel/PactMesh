"""Command line: run each participant as an independent process.

    python -m pactmesh relay    --port 8701
    python -m pactmesh ledger   --port 8702
    python -m pactmesh supplier --name alpha --price 90 --min-price 78
    python -m pactmesh buyer    --api-port 8700
    python -m pactmesh demo                 # orchestrates all of the above
    python -m pactmesh verify evidence.json
    python -m pactmesh eval                 # synthetic evaluation suite
    python -m pactmesh solana-anchor .pactmesh-demo/evidence.json --keypair devnet.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
from pathlib import Path

from .httpbase import run_in_thread

ROOT = Path(__file__).resolve().parents[1]


def _transport(args):
    from .transport import TransportUnavailable, make_transport

    try:
        t = make_transport(args.transport, args.relay, args.nym_client, args.relay_nym)
        if args.transport == "mixnet":
            t.fetch_adverts()  # prove the mixnet path works before doing anything
    except TransportUnavailable as e:
        sys.exit(f"[pactmesh] private mode requested but unavailable (no fallback to direct): {e}")
    return t


def cmd_relay(args):
    from .transport.relay import Relay

    relay = Relay(Path(args.observe) if args.observe else None)
    if args.nym_client:
        from .transport.nym import RelayNymGateway, TransportUnavailable

        try:
            gw = RelayNymGateway(relay, args.nym_client)
        except TransportUnavailable as e:
            sys.exit(f"[relay] private mode requested but unavailable: {e}")
        print(f"[relay] mixnet service address (give to agents as --relay-nym): {gw.address}", flush=True)

        def serve_nym():
            gw.serve_forever()
            os._exit(3)  # losing the mixnet side must be loud, never a silent downgrade

        threading.Thread(target=serve_nym, daemon=True).start()
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
    """Settlement backend: the SIMULATED ledger or Solana (Devnet or pactmesh-localnet)."""
    if getattr(args, "chain", "sim") == "solana":
        from .ledger.solana import DEVNET_RPC, SolanaEscrowClient

        if not args.program_id:
            sys.exit("[pactmesh] --program-id is required with --chain solana (see docs/SOLANA.md)")
        funder = None
        if getattr(args, "funder", None):
            from .ledger import Wallet

            funder = Wallet.from_solana_keypair(Path(args.funder)).key
        return SolanaEscrowClient(args.rpc or DEVNET_RPC, args.program_id, funder=funder)
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

    s = Supplier(Path(args.home or f".pactmesh/{args.name}"), args.name, _transport(args), _ledger(args),
                 price=args.price, min_price=args.min_price, delivery_seconds=args.delivery,
                 description=args.description, behavior=args.behavior)
    _use_keypair(s, args)
    _ensure_funds(s)
    print(f"[cripto:{args.name}] supplier key {s.identity.key_id[:16]}… price {args.price} (min {args.min_price})", flush=True)
    s.run()


def cmd_buyer(args):
    from .api import build_api
    from .buyer import Buyer
    from .decision import make_engine
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else None
    home = Path(args.home)
    b = Buyer(home, args.name, _transport(args), _ledger(args),
              engine=_engine(args), policy=policy)
    _use_keypair(b, args)
    _ensure_funds(b)
    print(f"[cripto:buyer] settlement {b.ledger.network} ({'SIMULATED' if b.ledger.simulated else 'on-chain'}), "
          f"payer {b.ledger.address}", flush=True)
    token_file = home / "admin_token"
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(24))
    token = token_file.read_text(encoding="utf-8").strip()
    dataset = Path(args.dataset).read_bytes() if args.dataset else None
    srv = build_api(b, token, dataset).serve(args.host, args.api_port)
    run_in_thread(srv)
    print(f"[cripto:buyer] dashboard http://{args.host}:{args.api_port}/#token={token}", flush=True)
    b.run()


def cmd_verify(args):
    from .audit import verify_package
    pkg = json.loads(Path(args.package).read_text(encoding="utf-8"))
    res = verify_package(pkg, _ledger(args) if (args.ledger or args.chain == "solana") else None)
    for c in res["checks"]:
        print(f"  [{'ok' if c['ok'] else 'FAIL'}] {c['check']} {c['detail']}")
    print("VALID" if res["ok"] else "INVALID")
    sys.exit(0 if res["ok"] else 1)


def _engine(args):
    from .decision import make_engine

    return make_engine(args.engine, args.model_url, args.model_name, args.model_revision,
                       os.environ.get("PACTMESH_MODEL_API_KEY"), args.laya_temperature, args.laya_abstain_below)


def model_args(p, default="reference"):
    from .decision import ENGINES

    p.add_argument("--engine", choices=ENGINES, default=default)
    p.add_argument("--model-url", help="http: full URL; openai-compat: base URL, e.g. http://127.0.0.1:11434/v1")
    p.add_argument("--model-name", help="openai-compat model name, e.g. laya or llama3.2")
    p.add_argument("--model-revision", default="unpinned", help="pin the checkpoint revision you evaluated")
    p.add_argument("--laya-temperature", type=float, default=1.0, help="temperature fitted by laya-bench (validation)")
    p.add_argument("--laya-abstain-below", type=float, default=0.0, help="abstain when choice probability is lower")


def cmd_eval(args):
    sys.path.insert(0, str(ROOT))
    from evaluation.run_eval import main

    main(args.out, extra=_engine(args) if args.engine != "reference" else None)


def cmd_solana_anchor(args):
    from nacl.signing import SigningKey

    from .ledger.solana import DEVNET_RPC, SolanaMemoAnchor

    secret = bytes(json.loads(Path(args.keypair).read_text(encoding="utf-8")))  # solana-keygen JSON (64 bytes)
    batch = json.loads(Path(args.package).read_text(encoding="utf-8"))["receipt"]["evidence_batch"]
    anchor = SolanaMemoAnchor(SigningKey(secret[:32]), args.rpc or DEVNET_RPC)
    print(f"[solana] payer {anchor.address} anchoring root {batch['root'][:16]}... (Devnet, memo)")
    sig = anchor.anchor(batch["batch_id"], batch["root"], batch["version"])
    print(f"[solana] signature {sig}\nhttps://explorer.solana.com/tx/{sig}?cluster=devnet")


def cmd_solana_keygen(args):
    from nacl.signing import SigningKey

    from .ledger.solana import DEVNET_RPC, SolanaMemoAnchor

    path = Path(args.out)
    if path.exists():
        key = SigningKey(bytes(json.loads(path.read_text(encoding="utf-8")))[:32])
        print(f"[solana] using existing {path}")
    else:
        key = SigningKey.generate()
        secret = list(bytes(key) + key.verify_key.encode())  # solana-keygen JSON format (64 bytes)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
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


def cmd_mcp(args):
    from .mcp import PactMeshMCP

    token = Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file else os.environ.get("PACTMESH_TOKEN", "")
    if not token:
        sys.exit("[mcp] provide --token-file or PACTMESH_TOKEN")
    PactMeshMCP(args.api, token).serve_stdio()


def cmd_model_server(args):
    from .modelserver import HashBackend, RuleBackend, TransformersBackend, build_app

    if args.backend == "rule":
        backend = RuleBackend()
    elif args.backend == "hash":
        backend = HashBackend()
    else:
        if not args.hf:
            sys.exit("[model-server] --hf <model id or local path> is required (e.g. convaiinnovations/laya)")
        backend = TransformersBackend(args.hf, args.revision, args.trust_remote_code)
    srv = build_app(backend, args.abstain_below).serve(args.host, args.port)
    print(f"[model-server] {backend.model_id}@{backend.revision} choice-scoring on "
          f"http://{args.host}:{args.port}/decide", flush=True)
    srv.serve_forever()


def cmd_laya_build(args):
    sys.path.insert(0, str(ROOT))
    from evaluation.laya_build import build

    m = build()
    print(json.dumps({k: {"items": v["items"], "by_type": v["by_type"], "sha256": v["sha256"][:16]}
                      for k, v in m["splits"].items()}, indent=1))


def cmd_laya_bench(args):
    sys.path.insert(0, str(ROOT))
    from evaluation.laya_run import DATA, run

    run(args.model_url, args.model_name)
    print((DATA / "results.md").read_text(encoding="utf-8"))


def cmd_laya_run(args):
    """Cross-platform version of scripts/run_laya.sh (works in Windows PowerShell)."""
    import subprocess
    import time as _t
    import urllib.request

    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except (ImportError, OSError) as e:
        sys.exit(f"[laya-run] cannot load torch/transformers: {type(e).__name__}: {e}\n"
                 "-> pip install -r requirements-model.txt (inside the activated virtual environment)")
    home = ROOT / ".pactmesh-laya"
    home.mkdir(exist_ok=True)
    log = home / "model-server.log"
    url = f"http://127.0.0.1:{args.port}"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    cmd = [sys.executable, "-m", "pactmesh", "model-server", "--hf", args.hf, "--port", str(args.port)]
    if args.revision:
        cmd += ["--revision", args.revision]
    if args.backend:
        cmd += ["--backend", args.backend]
    print(f"[laya-run] starting model server for {args.hf} (first run downloads the weights; log: {log})", flush=True)
    with open(log, "w", encoding="utf-8") as f:
        server = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
    try:
        start = _t.time()
        while True:
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as r:
                    print("[laya-run] model server ready:", r.read().decode(), flush=True)
                break
            except OSError:
                if server.poll() is not None:
                    tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
                    sys.exit(f"[laya-run] model server exited (code {server.returncode}):\n{tail}")
                if _t.time() - start > args.timeout:
                    sys.exit(f"[laya-run] model server not ready after {args.timeout}s; see {log}")
                _t.sleep(2)
        sys.path.insert(0, str(ROOT))
        from evaluation.laya_build import build
        from evaluation.laya_run import DATA, run

        if not (DATA / "manifest.json").exists():
            build()
        report = run(url, args.name)
        print((DATA / "results.md").read_text(encoding="utf-8"), flush=True)
        temp = report["engines"][-1]["tasks"]["choice"]["test"]["temperature"]
        print(f"[laya-run] calibrated choice temperature (validation split): {temp}", flush=True)
        base = [sys.executable, "-m", "pactmesh"]
        subprocess.run(base + ["eval", "--engine", "laya", "--model-url", url, "--laya-temperature", str(temp),
                               "--out", str(ROOT / "evaluation" / "results-model.json")], cwd=ROOT, check=True, env=env)
        if not args.skip_demo:
            subprocess.run(base + ["demo", "--engine", "laya+fallback", "--model-url", url,
                                   "--laya-temperature", str(temp)], cwd=ROOT, check=True, env=env)
        print("[laya-run] done: evaluation/laya/results.md and evaluation/results-model.json", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def cmd_bench(args):
    sys.path.insert(0, str(ROOT))
    from evaluation.bench import run

    out = args.out or str(ROOT / "evaluation" / f"bench-{args.chain}.json")
    run(args.n, args.chain, out)


def cmd_demo(args):
    from .demo import run_demo

    if args.chain == "solana" and not args.program_id:
        sys.exit("[pactmesh] demo --chain solana needs --program-id (and --rpc unless Devnet); see docs/SOLANA.md")
    run_demo(keep=args.keep, engine=args.engine, chain=args.chain, model_url=args.model_url,
             model_name=args.model_name, rpc=args.rpc, program_id=args.program_id, funder=args.funder)


def _preflight() -> None:
    """Fail with a clear message instead of a traceback when the environment is not set up."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows consoles/redirects: never crash on a character
        except (AttributeError, ValueError):
            pass
    if sys.version_info < (3, 10):
        sys.exit(f"[pactmesh] Python 3.10+ is required (this is {sys.version.split()[0]} at {sys.executable})")
    try:
        import nacl  # noqa: F401
    except ImportError:
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        sys.exit("[pactmesh] PyNaCl is not installed in this Python:\n  " + sys.executable + "\n"
                 + ("" if in_venv else "-> No virtual environment is active. Windows PowerShell: "
                    ".venv\\Scripts\\Activate.ps1   (Mac/Linux: source .venv/bin/activate)\n")
                 + "-> Then run: pip install -r requirements.txt -r requirements-dev.txt")


def main(argv=None):
    _preflight()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="pactmesh")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def chain(p):
        p.add_argument("--chain", choices=["sim", "solana"], default="sim")
        p.add_argument("--rpc", help="Solana RPC URL (default Devnet; use pactmesh-localnet for offline runs)")
        p.add_argument("--program-id", help="deployed pactmesh-escrow program id")

    def net(p):
        p.add_argument("--relay", action="append", default=None, help="relay URL (repeat for mirrors)")
        p.add_argument("--ledger", default="http://127.0.0.1:8702", help="SIMULATED ledger URL (--chain sim)")
        chain(p)
        p.add_argument("--keypair", help="solana-keygen JSON to use as this agent's payment key")
        p.add_argument("--funder", help="funded solana-keygen JSON that tops up an empty wallet (Devnet: no airdrops)")
        p.add_argument("--transport", choices=["direct", "mixnet"], default="direct")
        p.add_argument("--nym-client", default=None, help="local nym-client websocket, e.g. ws://127.0.0.1:1977")
        p.add_argument("--relay-nym", default=None, help="the relay's Nym address (mixnet mode)")

    p = sub.add_parser("relay"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8701)
    p.add_argument("--observe", help="write what the relay observes (headers only) to this file")
    p.add_argument("--nym-client", help="also serve this relay over the Nym mixnet via a local nym-client")
    p.set_defaults(fn=cmd_relay)
    p = sub.add_parser("ledger"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8702)
    p.add_argument("--db", default=".pactmesh/ledger.sqlite"); p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("supplier"); net(p); p.add_argument("--name", required=True); p.add_argument("--home")
    p.add_argument("--price", type=int, required=True); p.add_argument("--min-price", type=int, required=True)
    p.add_argument("--delivery", type=int, default=60); p.add_argument("--description", default="")
    p.add_argument("--behavior", choices=["honest", "bad_format", "wrong_values", "silent"], default="honest")
    p.set_defaults(fn=cmd_supplier)
    p = sub.add_parser("buyer"); net(p); p.add_argument("--name", default="cripto"); p.add_argument("--home", default=".pactmesh/buyer")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--api-port", type=int, default=8700)
    model_args(p); p.add_argument("--policy", help="policy JSON file")
    p.add_argument("--dataset", default=str(ROOT / "examples" / "dataset.csv")); p.set_defaults(fn=cmd_buyer)
    p = sub.add_parser("verify"); p.add_argument("package"); p.add_argument("--ledger"); chain(p); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("eval", help="compare engines on 300 synthetic scenarios (add yours with --engine)")
    p.add_argument("--out", default=str(ROOT / "evaluation" / "results.json")); model_args(p); p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("mcp", help="MCP server (stdio) exposing a running buyer to any MCP-capable AI agent")
    p.add_argument("--api", default="http://127.0.0.1:8700"); p.add_argument("--token-file", default=".pactmesh/buyer/admin_token")
    p.set_defaults(fn=cmd_mcp)
    p = sub.add_parser("model-server", help="serve a local model (e.g. Laya) as Cripto's decision engine")
    p.add_argument("--hf", help="Hugging Face model id or local path"); p.add_argument("--revision")
    p.add_argument("--backend", choices=["transformers", "rule", "hash"], default="transformers",
                   help="rule/hash are test stand-ins, not models")
    p.add_argument("--trust-remote-code", action="store_true", help="only if you reviewed the model's code")
    p.add_argument("--abstain-below", type=float, default=0.0, help="ABSTAIN when best-option probability is lower")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=9000)
    p.set_defaults(fn=cmd_model_server)
    p = sub.add_parser("laya-build", help="build the Laya-style benchmark splits (choice/score/binary)")
    p.set_defaults(fn=cmd_laya_build)
    p = sub.add_parser("laya-bench", help="run the Laya-style benchmark (add a served model with --model-url)")
    p.add_argument("--model-url", help="model server base URL, e.g. http://127.0.0.1:9000")
    p.add_argument("--model-name"); p.set_defaults(fn=cmd_laya_bench)
    p = sub.add_parser("laya-run", help="serve Laya, run the benchmark, calibrated safety eval and demo (any OS)")
    p.add_argument("--hf", default="convaiinnovations/laya"); p.add_argument("--revision")
    p.add_argument("--port", type=int, default=9000); p.add_argument("--name", help="label for the report")
    p.add_argument("--backend", choices=["transformers", "rule", "hash"], help=argparse.SUPPRESS)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for download + load")
    p.add_argument("--skip-demo", action="store_true"); p.set_defaults(fn=cmd_laya_run)
    p = sub.add_parser("bench", help="measure latency per stage and cost per contract")
    p.add_argument("--n", type=int, default=10); p.add_argument("--chain", choices=["sim", "localnet"], default="sim")
    p.add_argument("--out"); p.set_defaults(fn=cmd_bench)
    p = sub.add_parser("solana-keygen", help="create a Devnet-only keypair (and optionally request an airdrop)")
    p.add_argument("--out", default="devnet.json"); p.add_argument("--airdrop", action="store_true"); p.add_argument("--rpc")
    p.set_defaults(fn=cmd_solana_keygen)
    p = sub.add_parser("solana-anchor", help="anchor an evidence batch root on Solana Devnet (memo)")
    p.add_argument("package"); p.add_argument("--keypair", required=True); p.add_argument("--rpc")
    p.set_defaults(fn=cmd_solana_anchor)
    p = sub.add_parser("demo"); p.add_argument("--keep", action="store_true", help="keep processes running for the dashboard")
    model_args(p, default="simulated-llm")
    p.add_argument("--chain", choices=["sim", "localnet", "solana"], default="sim",
                   help="sim: SIMULATED ledger; localnet: Rust escrow program via pactmesh-localnet; "
                        "solana: the deployed program on a real cluster (solana-test-validator or Devnet)")
    p.add_argument("--rpc", help="with --chain solana: cluster RPC URL (default: Devnet)")
    p.add_argument("--program-id", help="with --chain solana: deployed pactmesh-escrow program id")
    p.add_argument("--funder", help="with --chain solana: funded solana-keygen JSON that tops up each agent "
                                    "(needed on Devnet, where airdrops are rate-limited)")
    p.set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    if hasattr(args, "relay") and not args.relay:
        args.relay = ["http://127.0.0.1:8701"]
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    args.fn(args)


if __name__ == "__main__":
    main()
