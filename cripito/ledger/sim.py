"""SIMULATED local ledger: escrow + anchoring, NOT a blockchain.

This process emulates the parts of a public chain that the protocol needs
(signed transactions, fees, slot-based expiry, confirmation levels,
escrow state machine, memo-style anchoring) so the full flow runs offline
and in tests. Everything on it is public to any client, like a real
chain: addresses, amounts and instructions are observable.

The demo labels it everywhere as SIMULATED; it must never be presented as
a deployed Solana program.

Escrow states: CREATED -> FUNDED -> RELEASED | REFUNDED | DISPUTED.
RELEASED and REFUNDED are terminal (no release-after-refund or vice versa).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..canonical import canonical, hash_obj
from ..crypto import verify_sig
from ..httpbase import App, HttpError, Request
from ..util import now

NETWORK = "cripito-sim-devnet"
TEST_MINT = "CRPT-TEST"
FEE_MINT = "SIM-SOL"
FEE = 5000
SLOT_SECONDS = 0.4
TX_VALID_SLOTS = 150  # like a recent blockhash window
FINALIZED_AFTER = 8
FAUCET = {TEST_MINT: 1000, FEE_MINT: 10_000_000}
TERMINAL = ("RELEASED", "REFUNDED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS balances (address TEXT, mint TEXT, amount INTEGER, PRIMARY KEY (address, mint));
CREATE TABLE IF NOT EXISTS txs (sig TEXT PRIMARY KEY, slot INTEGER, tx TEXT, ok INTEGER, err TEXT);
CREATE TABLE IF NOT EXISTS escrows (id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS anchors (batch_id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class TxError(Exception):
    pass


class SimLedger:
    def __init__(self, path: Path | str = ":memory:"):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        row = self.db.execute("SELECT v FROM meta WHERE k='genesis'").fetchone()
        if row:
            self.genesis = float(row["v"])
        else:
            self.genesis = time.time()
            self.db.execute("INSERT INTO meta VALUES ('genesis', ?)", (str(self.genesis),))
        self.app = App()
        self._routes()

    def slot(self) -> int:
        return int((now() - self.genesis) / SLOT_SECONDS) if now() > self.genesis else 0

    # ------------------------------------------------------------- balances

    def _bal(self, addr: str, mint: str) -> int:
        r = self.db.execute("SELECT amount FROM balances WHERE address=? AND mint=?", (addr, mint)).fetchone()
        return r["amount"] if r else 0

    def _add(self, addr: str, mint: str, delta: int) -> None:
        new = self._bal(addr, mint) + delta
        if new < 0:
            raise TxError("INSUFFICIENT_FUNDS")
        self.db.execute("INSERT OR REPLACE INTO balances VALUES (?,?,?)", (addr, mint, new))

    def faucet(self, address: str) -> dict:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            for mint, amt in FAUCET.items():
                self._add(address, mint, amt)
            self.db.execute("COMMIT")
        return {mint: self._bal(address, mint) for mint in FAUCET}

    # ----------------------------------------------------------- escrows

    def _escrow(self, eid: str) -> dict | None:
        r = self.db.execute("SELECT data FROM escrows WHERE id=?", (eid,)).fetchone()
        return json.loads(r["data"]) if r else None

    def _put_escrow(self, e: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO escrows VALUES (?,?)", (e["id"], json.dumps(e)))

    def _apply(self, tx: dict, signer: str, slot: int) -> dict:
        kind = tx["kind"]
        p = tx["params"]
        if kind == "escrow_create":
            eid = "esc_" + hash_obj({"agreement_hash": p["agreement_hash"]})[:40]
            if self._escrow(eid):
                raise TxError("ESCROW_EXISTS")
            if p["payer"] != signer:
                raise TxError("WRONG_AUTHORITY")
            if p["mint"] != TEST_MINT:
                raise TxError("MINT_NOT_ALLOWED")
            if int(p["amount"]) <= 0:
                raise TxError("BAD_AMOUNT")
            e = {"id": eid, "state": "CREATED", "payer": p["payer"], "payee": p["payee"], "mint": p["mint"],
                 "amount": str(int(p["amount"])), "agreement_hash": p["agreement_hash"],
                 "deadline": int(p["deadline"]), "history": [["CREATED", slot]]}
            self._put_escrow(e)
            return {"escrow_id": eid}
        e = self._escrow(p.get("escrow_id", ""))
        if kind == "anchor":
            if self.db.execute("SELECT 1 FROM anchors WHERE batch_id=?", (p["batch_id"],)).fetchone():
                raise TxError("ANCHOR_EXISTS")
            data = {"batch_id": p["batch_id"], "root": p["root"], "version": p["version"], "slot": slot,
                    "signer": signer}
            self.db.execute("INSERT INTO anchors VALUES (?,?)", (p["batch_id"], json.dumps(data)))
            return {"anchored": p["batch_id"]}
        if not e:
            raise TxError("ESCROW_NOT_FOUND")
        if e["state"] in TERMINAL:
            raise TxError("ESCROW_TERMINAL")
        if kind == "escrow_fund":
            if signer != e["payer"]:
                raise TxError("WRONG_AUTHORITY")
            if e["state"] != "CREATED":
                raise TxError("BAD_STATE")
            if p["mint"] != e["mint"] or str(int(p["amount"])) != e["amount"]:
                raise TxError("AMOUNT_OR_MINT_MISMATCH")
            self._add(signer, e["mint"], -int(e["amount"]))
            e["state"] = "FUNDED"
        elif kind == "escrow_release":
            if signer != e["payer"]:
                raise TxError("WRONG_AUTHORITY")
            if e["state"] != "FUNDED":
                raise TxError("BAD_STATE")
            if p["payee"] != e["payee"]:
                raise TxError("PAYEE_MISMATCH")
            self._add(e["payee"], e["mint"], int(e["amount"]))
            e["state"] = "RELEASED"
        elif kind == "escrow_refund":
            if signer != e["payer"]:
                raise TxError("WRONG_AUTHORITY")
            if e["state"] not in ("CREATED", "FUNDED"):
                raise TxError("BAD_STATE")
            if now() < e["deadline"]:
                raise TxError("DEADLINE_NOT_REACHED")
            if e["state"] == "FUNDED":
                self._add(e["payer"], e["mint"], int(e["amount"]))
            e["state"] = "REFUNDED"
        elif kind == "escrow_dispute":
            if signer not in (e["payer"], e["payee"]):
                raise TxError("WRONG_AUTHORITY")
            if e["state"] != "FUNDED":
                raise TxError("BAD_STATE")
            e["state"] = "DISPUTED"
        else:
            raise TxError("UNKNOWN_INSTRUCTION")
        e["history"].append([e["state"], slot])
        self._put_escrow(e)
        return {"escrow_id": e["id"], "state": e["state"]}

    def submit(self, signed: dict) -> dict:
        tx, sig = signed.get("tx"), signed.get("signature")
        if not isinstance(tx, dict) or not isinstance(sig, str):
            raise HttpError(400, "BAD_TX")
        signer = tx.get("signer", "")
        if not verify_sig(signer, canonical(tx), sig):
            raise HttpError(400, "BAD_SIGNATURE")
        with self.lock:
            prev = self.db.execute("SELECT * FROM txs WHERE sig=?", (sig,)).fetchone()
            if prev:  # resubmission of the same signed tx is idempotent
                return self._status_row(prev)
            slot = self.slot()
            if not isinstance(tx.get("recent_slot"), int) or not slot - TX_VALID_SLOTS <= tx["recent_slot"] <= slot:
                raise HttpError(400, "TX_EXPIRED")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._add(signer, FEE_MINT, -FEE)
                self.db.execute("SAVEPOINT ix")
                try:
                    result = self._apply(tx, signer, slot)
                    ok, err = 1, None
                    self.db.execute("RELEASE ix")
                except (TxError, KeyError, ValueError, TypeError) as e:
                    self.db.execute("ROLLBACK TO ix")
                    self.db.execute("RELEASE ix")
                    ok, err, result = 0, str(e) or type(e).__name__, None
                self.db.execute("INSERT INTO txs VALUES (?,?,?,?,?)",
                                (sig, slot, json.dumps({"tx": tx, "result": result}), ok, err))
                self.db.execute("COMMIT")
            except TxError as e:  # cannot pay fee
                self.db.execute("ROLLBACK")
                raise HttpError(400, str(e))
            return self._status_row(self.db.execute("SELECT * FROM txs WHERE sig=?", (sig,)).fetchone())

    def _status_row(self, row) -> dict:
        age = self.slot() - row["slot"]
        level = "finalized" if age >= FINALIZED_AFTER else ("confirmed" if age >= 1 else "processed")
        body = json.loads(row["tx"])
        return {"signature": row["sig"], "slot": row["slot"], "ok": bool(row["ok"]), "err": row["err"],
                "confirmation": level, "result": body["result"], "kind": body["tx"]["kind"]}

    # --------------------------------------------------------------- http

    def _routes(self):
        app = self.app

        @app.route("GET", "/health")
        def health(req):
            return {"ok": True, "network": NETWORK, "simulated": True, "slot": self.slot()}

        @app.route("GET", "/slot")
        def slot(req):
            return {"slot": self.slot()}

        @app.route("POST", "/faucet")
        def faucet(req: Request):
            addr = (req.json() or {}).get("address", "")
            if len(addr) != 64:
                raise HttpError(400, "BAD_ADDRESS")
            return self.faucet(addr)

        @app.route("GET", "/balance/([0-9a-f]{64})")
        def balance(req, addr):
            with self.lock:
                rows = self.db.execute("SELECT mint, amount FROM balances WHERE address=?", (addr,)).fetchall()
            return {r["mint"]: str(r["amount"]) for r in rows}

        @app.route("POST", "/tx")
        def tx(req: Request):
            return self.submit(req.json())

        @app.route("GET", "/tx/([0-9a-f]{128})")
        def tx_status(req, sig):
            with self.lock:
                row = self.db.execute("SELECT * FROM txs WHERE sig=?", (sig,)).fetchone()
            if not row:
                raise HttpError(404, "NOT_FOUND")
            return self._status_row(row)

        @app.route("GET", "/escrow/(esc_[0-9a-f]{40})")
        def escrow(req, eid):
            with self.lock:
                e = self._escrow(eid)
            if not e:
                raise HttpError(404, "NOT_FOUND")
            return e

        @app.route("GET", "/anchor/([0-9a-f]{32})")
        def anchor(req, bid):
            with self.lock:
                r = self.db.execute("SELECT data FROM anchors WHERE batch_id=?", (bid,)).fetchone()
            if not r:
                raise HttpError(404, "NOT_FOUND")
            return json.loads(r["data"])

        @app.route("GET", "/explorer")
        def explorer(req):
            # Public view: exactly what any chain observer would see.
            with self.lock:
                rows = self.db.execute("SELECT * FROM txs ORDER BY slot DESC LIMIT 200").fetchall()
            return {"network": NETWORK, "simulated": True,
                    "txs": [{**self._status_row(r), "tx": json.loads(r["tx"])["tx"]} for r in rows]}
