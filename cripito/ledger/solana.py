"""Solana Devnet anchoring through the SPL Memo program (no extra deps).

Publishes ``cripito:anchor:v1:<merkle-version>:<batch_id>:<root>`` as a
memo signed by a dedicated Devnet keypair. This anchors evidence roots
only; the escrow program on Solana is future work, so payments in the demo
run on the SIMULATED ledger and are labelled as such.

Status: transaction encoding is unit-tested offline. It has NOT been
exercised against Devnet from the build environment (outbound access to
the RPC was blocked), so treat it as untested until you run
``python -m cripito solana-anchor`` yourself.
"""

from __future__ import annotations

import base64
import json
import urllib.request

from nacl.signing import SigningKey

DEVNET_RPC = "https://api.devnet.solana.com"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


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


def memo_text(batch_id: str, root: str, version: str) -> str:
    return f"cripito:anchor:v1:{version}:{batch_id}:{root}"


def build_memo_tx(key: SigningKey, memo: str, recent_blockhash: str) -> bytes:
    payer = key.verify_key.encode()
    data = memo.encode()
    message = (
        bytes([1, 0, 1])  # 1 signer (payer), 0 readonly signed, 1 readonly unsigned (memo program)
        + compact_u16(2) + payer + b58decode(MEMO_PROGRAM)
        + b58decode(recent_blockhash)
        + compact_u16(1)  # one instruction
        + bytes([1]) + compact_u16(0) + compact_u16(len(data)) + data
    )
    sig = key.sign(message).signature
    return compact_u16(1) + sig + message


class SolanaMemoAnchor:
    network = "solana-devnet"
    simulated = False

    def __init__(self, key: SigningKey, rpc: str = DEVNET_RPC):
        self.key, self.rpc = key, rpc

    @property
    def address(self) -> str:
        return b58encode(self.key.verify_key.encode())

    def _rpc(self, method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.rpc, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(out["error"])
        return out["result"]

    def anchor(self, batch_id: str, root: str, version: str) -> str:
        bh = self._rpc("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"]
        tx = build_memo_tx(self.key, memo_text(batch_id, root, version), bh)
        return self._rpc("sendTransaction", [base64.b64encode(tx).decode(), {"encoding": "base64"}])

    def status(self, signature: str) -> dict | None:
        res = self._rpc("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        return res["value"][0]
