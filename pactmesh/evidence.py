"""Evidence: hiding commitments, Merkle batches and inclusion proofs.

commitment = SHA256( lp(domain) || lp(randomness32) || lp(canonical_event) )
where lp(x) = uint32_be(len(x)) || x, so concatenation is unambiguous.

Merkle tree (version ``pactmesh-merkle/1``):
  leaf  = SHA256(0x00 || commitment)
  node  = SHA256(0x01 || left || right)
  leaves keep insertion order; an unpaired node is promoted unchanged to
  the next level (never duplicated).
"""

from __future__ import annotations

import hashlib
import os
import struct

from .canonical import canonical

COMMIT_DOMAIN = b"pactmesh/evidence-commitment/v1"
MERKLE_VERSION = "pactmesh-merkle/1"


def _lp(b: bytes) -> bytes:
    return struct.pack(">I", len(b)) + b


def new_randomness() -> bytes:
    return os.urandom(32)


def commitment(event: dict, randomness: bytes) -> str:
    if len(randomness) != 32:
        raise ValueError("randomness must be 32 bytes")
    h = hashlib.sha256(_lp(COMMIT_DOMAIN) + _lp(randomness) + _lp(canonical(event)))
    return h.hexdigest()


def _leaf(c_hex: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(c_hex)).digest()


def _node(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + a + b).digest()


def merkle_root(commitments: list[str]) -> str:
    if not commitments:
        raise ValueError("empty batch")
    level = [_leaf(c) for c in commitments]
    while len(level) > 1:
        nxt = [_node(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0].hex()


def inclusion_proof(commitments: list[str], index: int) -> list[dict]:
    """List of {side, hash} siblings from leaf to root."""
    level = [_leaf(c) for c in commitments]
    proof = []
    while len(level) > 1:
        sib = index ^ 1
        if sib < len(level):
            proof.append({"side": "left" if sib < index else "right", "hash": level[sib].hex()})
        nxt = [_node(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
        index //= 2
    return proof


def verify_inclusion(commitment_hex: str, proof: list[dict], root_hex: str) -> bool:
    try:
        h = _leaf(commitment_hex)
        for step in proof:
            sib = bytes.fromhex(step["hash"])
            h = _node(sib, h) if step["side"] == "left" else _node(h, sib)
        return h.hex() == root_hex
    except (KeyError, ValueError, TypeError):
        return False
