"""Chain adapters. The negotiation protocol is chain-independent; the
adapter validates network and asset identifiers, the model never does.

* ``SimLedgerClient`` talks to the SIMULATED local ledger (offline demo).
* ``solana.SolanaEscrowClient`` drives the ``contracts/escrow`` program on
  Solana (Devnet or the offline ``cripito-localnet`` emulator) and anchors
  Merkle roots via the Memo program.

Payment (wallet) keys live in their own file, separate from message keys,
and only the executor holds them.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from nacl.signing import SigningKey

from ..canonical import canonical, hash_obj
from ..crypto import random_id
from ..httpbase import HttpError, call
from .sim import FEE_MINT, NETWORK, TEST_MINT

LEVELS = {"processed": 0, "confirmed": 1, "finalized": 2}


def escrow_id_for(agreement_hash: str) -> str:
    return "esc_" + hash_obj({"agreement_hash": agreement_hash})[:40]


class Wallet:
    def __init__(self, key: SigningKey):
        self.key = key

    @classmethod
    def load_or_create(cls, path: Path) -> "Wallet":
        if path.exists():
            return cls(SigningKey(bytes.fromhex(json.loads(path.read_text())["seed"])))
        seed = os.urandom(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"seed": seed.hex(), "note": "SIMULATED devnet wallet - no real assets"}, f)
        return cls(SigningKey(seed))

    @property
    def address(self) -> str:
        return self.key.verify_key.encode().hex()

    @classmethod
    def from_solana_keypair(cls, path: Path) -> "Wallet":
        """Import a solana-keygen JSON keypair (64 bytes: seed || public key)."""
        raw = bytes(json.loads(path.read_text()))
        key = SigningKey(raw[:32])
        if len(raw) != 64 or key.verify_key.encode() != raw[32:]:
            raise ValueError(f"{path} is not a valid solana-keygen keypair")
        return cls(key)


class LedgerError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code} {detail}".strip())
        self.code = code


class SimLedgerClient:
    simulated = True
    network = NETWORK
    test_mint = TEST_MINT
    fee_mint = FEE_MINT

    def __init__(self, url: str, wallet: Wallet | None = None):
        self.url, self.wallet = url.rstrip("/"), wallet

    def _get(self, path: str):
        try:
            return call("GET", self.url + path, timeout=5)
        except HttpError as e:
            if e.status == 404:
                return None
            raise LedgerError(e.code, e.detail) from None
        except OSError as e:
            raise LedgerError("RPC_UNAVAILABLE", str(e)) from None

    def send(self, kind: str, params: dict) -> dict:
        if not self.wallet:
            raise LedgerError("NO_WALLET")
        slot = self._get("/slot")["slot"]
        tx = {"kind": kind, "params": params, "signer": self.wallet.address, "recent_slot": slot,
              "nonce": random_id(8)}
        sig = self.wallet.key.sign(canonical(tx)).signature.hex()
        return self.submit({"tx": tx, "signature": sig})

    def submit(self, signed: dict) -> dict:
        try:
            return call("POST", self.url + "/tx", signed, timeout=10)
        except HttpError as e:
            raise LedgerError(e.code, e.detail) from None
        except OSError as e:
            raise LedgerError("RPC_UNAVAILABLE", str(e)) from None

    def health(self) -> dict | None:
        try:
            return self._get("/health")
        except LedgerError:
            return None

    def faucet(self) -> dict:
        return call("POST", self.url + "/faucet", {"address": self.wallet.address})

    def balance(self, address: str | None = None) -> dict:
        return self._get(f"/balance/{address or self.wallet.address}") or {}

    def tx_status(self, sig: str) -> dict | None:
        return self._get(f"/tx/{sig}")

    def get_escrow(self, escrow_id: str) -> dict | None:
        return self._get(f"/escrow/{escrow_id}")

    @property
    def address(self) -> str:
        return self.wallet.address

    def escrow_id_for(self, agreement_hash: str) -> str:
        return escrow_id_for(agreement_hash)

    def ensure_funds(self) -> None:
        if int(self.balance().get(FEE_MINT, "0")) == 0:
            self.faucet()

    def get_anchor(self, batch_id: str, tx: str | None = None) -> dict | None:
        return self._get(f"/anchor/{batch_id}")

    def wait(self, sig: str, level: str = "confirmed", timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        while True:
            st = self.tx_status(sig)
            if st and LEVELS[st["confirmation"]] >= LEVELS[level]:
                return st
            if time.time() > deadline:
                raise LedgerError("CONFIRMATION_TIMEOUT", sig)
            time.sleep(0.2)

    # Instructions -------------------------------------------------------

    def create_escrow(self, agreement: dict, agreement_hash: str, deadline: int) -> dict:
        return self.send("escrow_create", {
            "payer": self.wallet.address, "payee": agreement["payee"], "mint": agreement["asset"],
            "amount": agreement["price"], "agreement_hash": agreement_hash, "deadline": deadline})

    def fund_escrow(self, escrow_id: str, amount: str, mint: str) -> dict:
        return self.send("escrow_fund", {"escrow_id": escrow_id, "amount": amount, "mint": mint})

    def release(self, escrow_id: str, payee: str) -> dict:
        return self.send("escrow_release", {"escrow_id": escrow_id, "payee": payee})

    def refund(self, escrow_id: str) -> dict:
        return self.send("escrow_refund", {"escrow_id": escrow_id})

    def dispute(self, escrow_id: str) -> dict:
        return self.send("escrow_dispute", {"escrow_id": escrow_id})

    def anchor(self, batch_id: str, root: str, version: str) -> dict:
        return self.send("anchor", {"batch_id": batch_id, "root": root, "version": version})
