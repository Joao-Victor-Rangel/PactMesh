"""Solana Devnet adapter (no extra Python deps).

* ``SolanaMemoAnchor``: anchors Merkle roots as SPL Memo transactions.
* ``SolanaEscrowClient``: drives the ``contracts/escrow`` program (native SOL
  held by a PDA keyed by the agreement hash) and exposes the same interface
  as ``SimLedgerClient``, so buyer and supplier run unchanged on either.

Amounts on Solana are lamports (the asset is ``SOL`` on ``solana-devnet``).

Status: transaction/PDA encoding is unit-tested offline and cross-checked
against the Rust program's own derivation (contracts/escrow/fixtures.json).
It has NOT been exercised against a live Devnet from the build environment
(the RPC was unreachable there). Test it with ``python -m pactmesh solana-anchor``
and the Devnet guide in docs/SOLANA.md before relying on it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import time
import urllib.error
import urllib.request

from nacl.signing import SigningKey

from . import LEVELS, LedgerError

DEVNET_RPC = "https://api.devnet.solana.com"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
ESCROW_SEED = b"pactmesh-escrow"
NETWORK = "solana-devnet"
ASSET = "SOL"
# Top-up from a funder key: a rent-exempt system account plus fees for a demo run (0.01 SOL).
FUND_LAMPORTS = 10_000_000
ESCROW_LEN = 115
STATES = ["CREATED", "FUNDED", "RELEASED", "REFUNDED", "DISPUTED"]
ERRORS = {1: "WRONG_AUTHORITY", 2: "BAD_STATE", 3: "PAYEE_MISMATCH", 4: "AMOUNT_OR_MINT_MISMATCH",
          5: "DEADLINE_NOT_REACHED", 6: "ESCROW_TERMINAL", 7: "INVALID_PDA", 8: "INVALID_INSTRUCTION",
          9: "ESCROW_EXISTS", 10: "INVALID_ACCOUNT_DATA", 11: "BAD_AMOUNT"}
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# ------------------------------------------------------------------ encoding


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def compact_u16(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


# Ed25519 curve check (same rule as curve25519-dalek CompressedEdwardsY::decompress).
_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def is_on_curve(b: bytes) -> bool:
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    y2 = y * y % _P
    u, v = (y2 - 1) % _P, (_D * y2 + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


def find_program_address(seeds: list[bytes], program_id: bytes) -> tuple[bytes, int]:
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + program_id + b"ProgramDerivedAddress").digest()
        if not is_on_curve(h):
            return h, bump
    raise ValueError("no viable bump")


def escrow_pda(program_id: bytes, agreement_hash_hex: str) -> tuple[bytes, int]:
    return find_program_address([ESCROW_SEED, bytes.fromhex(agreement_hash_hex)], program_id)


def build_tx(key: SigningKey, instructions: list[tuple[bytes, list[tuple[bytes, bool, bool]], bytes]],
             recent_blockhash: str) -> tuple[bytes, str]:
    """Legacy transaction with a single signer (the fee payer).
    instructions: (program_id, [(pubkey, is_signer, is_writable)], data)."""
    payer = key.verify_key.encode()
    metas: dict[bytes, list[bool]] = {payer: [True, True]}
    for pid, accs, _ in instructions:
        for pk, s, w in accs:
            m = metas.setdefault(pk, [False, False])
            m[0] |= s
            m[1] |= w
        metas.setdefault(pid, [False, False])
    if any(s for pk, (s, _) in metas.items() if pk != payer):
        raise ValueError("only the fee payer may sign in this client")
    others = [pk for pk in metas if pk != payer]
    order = [payer] + [pk for pk in others if metas[pk][1]] + [pk for pk in others if not metas[pk][1]]
    index = {pk: i for i, pk in enumerate(order)}
    readonly_unsigned = sum(1 for pk in others if not metas[pk][1])
    msg = bytearray([1, 0, readonly_unsigned])
    msg += compact_u16(len(order)) + b"".join(order) + b58decode(recent_blockhash)
    msg += compact_u16(len(instructions))
    for pid, accs, data in instructions:
        msg += bytes([index[pid]]) + compact_u16(len(accs)) + bytes(index[pk] for pk, _, _ in accs)
        msg += compact_u16(len(data)) + data
    sig = key.sign(bytes(msg)).signature
    return compact_u16(1) + sig + bytes(msg), b58encode(sig)


def memo_text(batch_id: str, root: str, version: str) -> str:
    return f"pactmesh:anchor:v1:{version}:{batch_id}:{root}"


def build_memo_tx(key: SigningKey, memo: str, recent_blockhash: str) -> bytes:
    return build_tx(key, [(b58decode(MEMO_PROGRAM), [], memo.encode())], recent_blockhash)[0]


def escrow_ix_data(tag: int, *, agreement_hash: str = "", payee: bytes = b"", amount: int = 0,
                   deadline: int = 0) -> bytes:
    if tag == 0:
        return (bytes([0]) + bytes.fromhex(agreement_hash) + payee + amount.to_bytes(8, "little")
                + deadline.to_bytes(8, "little", signed=True))
    if tag == 1:
        return bytes([1]) + amount.to_bytes(8, "little")
    return bytes([tag])


def parse_escrow(data: bytes) -> dict:
    if len(data) < ESCROW_LEN or data[0] != 1:
        raise ValueError("not a pactmesh escrow account")
    return {"state": STATES[data[1]], "bump": data[2], "payer": b58encode(data[3:35]),
            "payee": b58encode(data[35:67]), "amount": str(int.from_bytes(data[67:75], "little")),
            "agreement_hash": data[75:107].hex(), "deadline": int.from_bytes(data[107:115], "little", signed=True),
            "mint": ASSET}


# ---------------------------------------------------------------------- RPC


class _Rpc:
    def __init__(self, url: str):
        self.url = url

    def __call__(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                out = json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:
            raise LedgerError("RPC_UNAVAILABLE", str(e)) from None
        if "error" in out:
            raise LedgerError("RPC_ERROR", json.dumps(out["error"])[:500])
        return out["result"]


def _custom_error(err) -> str:
    """Map {"InstructionError":[0,{"Custom":n}]} (or preflight text) to our codes."""
    text = json.dumps(err)
    m = re.search(r'"Custom":\s*(\d+)', text) or re.search(r"custom program error: 0x([0-9a-f]+)", text)
    if m:
        n = int(m.group(1), 16) if "0x" in m.group(0) else int(m.group(1))
        return ERRORS.get(n, f"CUSTOM_{n}")
    return text[:200]


class SolanaMemoAnchor:
    network = NETWORK
    simulated = False

    def __init__(self, key: SigningKey, rpc: str = DEVNET_RPC):
        self.key, self._rpc = key, _Rpc(rpc)

    @property
    def address(self) -> str:
        return b58encode(self.key.verify_key.encode())

    def anchor(self, batch_id: str, root: str, version: str) -> str:
        bh = self._rpc("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"]
        tx = build_memo_tx(self.key, memo_text(batch_id, root, version), bh)
        return self._rpc("sendTransaction", [base64.b64encode(tx).decode(), {"encoding": "base64"}])


class SolanaEscrowClient:
    """Same surface as SimLedgerClient, backed by Solana Devnet."""

    simulated = False
    network = NETWORK
    test_mint = ASSET

    def __init__(self, rpc: str, program_id: str, wallet=None, funder: SigningKey | None = None):
        self.rpc = _Rpc(rpc)
        self.program_id = b58decode(program_id)
        self.wallet = wallet
        self.funder = funder
        self._failed: dict[str, str] = {}

    @property
    def address(self) -> str:
        return b58encode(self.wallet.key.verify_key.encode())

    def escrow_id_for(self, agreement_hash: str) -> str:
        return b58encode(escrow_pda(self.program_id, agreement_hash)[0])

    # ---------------------------------------------------------- queries

    def health(self) -> dict | None:
        try:
            return {"ok": self.rpc("getHealth", []) == "ok", "network": NETWORK, "simulated": False,
                    "slot": self.rpc("getSlot", [])}
        except LedgerError:
            return None

    def balance(self, address: str | None = None) -> dict:
        lamports = self.rpc("getBalance", [address or self.address])["value"]
        return {ASSET: str(lamports)}

    def faucet(self) -> dict:
        if self.funder is None:
            return {"airdrop": self.rpc("requestAirdrop", [self.address, 1_000_000_000])}
        # Public clusters rate-limit airdrops, and a payee account must be rent-exempt to receive a
        # payment at all: top the wallet up from a funded Devnet key instead (system transfer).
        ix = (b58decode(SYSTEM_PROGRAM), [(self.funder.verify_key.encode(), True, True),
                                          (self.wallet.key.verify_key.encode(), False, True)],
              struct.pack("<IQ", 2, FUND_LAMPORTS))
        bh = self.rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
        raw, sig = build_tx(self.funder, [ix], bh)
        self.rpc("sendTransaction", [base64.b64encode(raw).decode(), {"encoding": "base64",
                                                                      "preflightCommitment": "confirmed"}])
        return {"transfer": self.wait(sig, "confirmed")}

    def ensure_funds(self) -> None:
        if int(self.balance()[ASSET]) == 0:
            try:
                self.faucet()
            except LedgerError as e:
                raise LedgerError("NO_FUNDS", f"fund {self.address} at https://faucet.solana.com ({e})") from None

    def get_escrow(self, escrow_id: str) -> dict | None:
        info = self.rpc("getAccountInfo", [escrow_id, {"encoding": "base64", "commitment": "confirmed"}])["value"]
        if not info or b58decode(info["owner"]) != self.program_id:
            return None
        try:
            e = parse_escrow(base64.b64decode(info["data"][0]))
        except ValueError:
            return None
        return {"id": escrow_id, **e}

    def tx_status(self, sig: str | None) -> dict | None:
        if not sig:
            return None
        if sig in self._failed:
            return {"signature": sig, "ok": False, "err": self._failed[sig], "confirmation": "processed", "slot": None}
        st = self.rpc("getSignatureStatuses", [[sig], {"searchTransactionHistory": True}])["value"][0]
        if not st:
            return None
        level = st.get("confirmationStatus") or "processed"
        return {"signature": sig, "ok": st.get("err") is None, "err": _custom_error(st["err"]) if st.get("err") else None,
                "confirmation": level, "slot": st.get("slot")}

    def wait(self, sig: str, level: str = "confirmed", timeout: float = 60.0) -> dict:
        deadline = time.time() + timeout
        while True:
            st = self.tx_status(sig)
            if st and (not st["ok"] or LEVELS[st["confirmation"]] >= LEVELS[level]):
                return st
            if time.time() > deadline:
                raise LedgerError("CONFIRMATION_TIMEOUT", sig)
            time.sleep(0.5)

    def get_anchor(self, batch_id: str, tx: str | None = None) -> dict | None:
        if not tx:
            return None
        t = self.rpc("getTransaction", [tx, {"encoding": "json", "maxSupportedTransactionVersion": 0,
                                             "commitment": "confirmed"}])
        if not t or (t.get("meta") or {}).get("err"):
            return None
        for line in t["meta"].get("logMessages") or []:
            m = re.search(r"pactmesh:anchor:v1:([^:]+):([0-9a-f]{32}):([0-9a-f]{64})", line)
            if m and m.group(2) == batch_id:
                return {"batch_id": batch_id, "version": m.group(1), "root": m.group(3), "slot": t["slot"], "tx": tx}
        return None

    # ----------------------------------------------------------- sending

    def _send(self, instructions, result: dict) -> dict:
        bh = self.rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"]
        raw, sig = build_tx(self.wallet.key, instructions, bh)
        try:
            self.rpc("sendTransaction", [base64.b64encode(raw).decode(), {"encoding": "base64",
                                                                          "preflightCommitment": "confirmed"}])
        except LedgerError as e:
            if e.code == "RPC_UNAVAILABLE":
                raise
            if "already been processed" in str(e):  # identical signed tx resent: it landed, idempotent
                return {"signature": sig, "ok": True, "err": None, "confirmation": "processed", "slot": None,
                        "result": result}
            self._failed[sig] = _custom_error(str(e))  # preflight rejected: never landed
            return {"signature": sig, "ok": False, "err": self._failed[sig], "confirmation": "processed",
                    "slot": None, "result": None}
        return {"signature": sig, "ok": True, "err": None, "confirmation": "processed", "slot": None, "result": result}

    def _escrow_ix(self, data: bytes, accounts: list[tuple[bytes, bool, bool]]):
        return (self.program_id, accounts, data)

    def create_escrow(self, agreement: dict, agreement_hash: str, deadline: int) -> dict:
        if agreement["asset"] != ASSET:
            raise LedgerError("MINT_NOT_ALLOWED", agreement["asset"])
        pda, _ = escrow_pda(self.program_id, agreement_hash)
        me = self.wallet.key.verify_key.encode()
        data = escrow_ix_data(0, agreement_hash=agreement_hash, payee=b58decode(agreement["payee"]),
                              amount=int(agreement["price"]), deadline=deadline)
        ix = self._escrow_ix(data, [(me, True, True), (pda, False, True), (b58decode(SYSTEM_PROGRAM), False, False)])
        return self._send([ix], {"escrow_id": b58encode(pda)})

    def fund_escrow(self, escrow_id: str, amount: str, mint: str) -> dict:
        if mint != ASSET:
            raise LedgerError("MINT_NOT_ALLOWED", mint)
        me = self.wallet.key.verify_key.encode()
        ix = self._escrow_ix(escrow_ix_data(1, amount=int(amount)),
                             [(me, True, True), (b58decode(escrow_id), False, True), (b58decode(SYSTEM_PROGRAM), False, False)])
        return self._send([ix], {"escrow_id": escrow_id, "state": "FUNDED"})

    def release(self, escrow_id: str, payee: str) -> dict:
        me = self.wallet.key.verify_key.encode()
        ix = self._escrow_ix(escrow_ix_data(2), [(me, True, False), (b58decode(escrow_id), False, True),
                                                 (b58decode(payee), False, True)])
        return self._send([ix], {"escrow_id": escrow_id, "state": "RELEASED"})

    def refund(self, escrow_id: str) -> dict:
        me = self.wallet.key.verify_key.encode()
        ix = self._escrow_ix(escrow_ix_data(3), [(me, True, True), (b58decode(escrow_id), False, True)])
        return self._send([ix], {"escrow_id": escrow_id, "state": "REFUNDED"})

    def dispute(self, escrow_id: str) -> dict:
        me = self.wallet.key.verify_key.encode()
        ix = self._escrow_ix(escrow_ix_data(4), [(me, True, False), (b58decode(escrow_id), False, True)])
        return self._send([ix], {"escrow_id": escrow_id, "state": "DISPUTED"})

    def anchor(self, batch_id: str, root: str, version: str) -> dict:
        ix = (b58decode(MEMO_PROGRAM), [], memo_text(batch_id, root, version).encode())
        return self._send([ix], {"anchored": batch_id})
