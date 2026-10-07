"""Cross-language checks: Python client vs the Rust escrow program."""

import json
from pathlib import Path

from nacl.signing import SigningKey

from cripito.ledger import Wallet
from cripito.ledger.solana import (SYSTEM_PROGRAM, SolanaEscrowClient, b58decode, b58encode, build_tx,
                                   escrow_ix_data, escrow_pda, is_on_curve, parse_escrow)

FIX = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "escrow" / "fixtures.json").read_text())


def test_pda_matches_rust_program():
    pda, bump = escrow_pda(bytes.fromhex(FIX["program_id_hex"]), FIX["agreement_hash"])
    assert pda.hex() == FIX["escrow_pda_hex"]
    assert bump == FIX["bump"]
    assert b58encode(pda) == FIX["escrow_pda_base58"]
    assert not is_on_curve(pda)


def test_pda_search_skips_on_curve_candidates():
    # Over many hashes some first candidates land on the curve; derivation must keep searching.
    pid = bytes(range(32))
    bumps = {escrow_pda(pid, f"{i:064x}")[1] for i in range(64)}
    assert 255 in bumps and len(bumps) > 1


def test_real_public_keys_are_on_curve():
    for i in range(20):
        assert is_on_curve(SigningKey(bytes([i]) * 32).verify_key.encode())


def test_create_instruction_bytes_match_rust():
    data = escrow_ix_data(0, agreement_hash=FIX["agreement_hash"], payee=bytes.fromhex(FIX["payee_hex"]),
                          amount=FIX["amount"], deadline=FIX["deadline"])
    assert data.hex() == FIX["create_data_hex"]


def test_escrow_tx_account_ordering(tmp_path):
    w = Wallet.load_or_create(tmp_path / "w.json")
    pid = b58encode(bytes.fromhex(FIX["program_id_hex"]))
    c = SolanaEscrowClient("http://127.0.0.1:9", pid, w)
    pda = b58decode(c.escrow_id_for(FIX["agreement_hash"]))
    me = w.key.verify_key.encode()
    ix = (c.program_id, [(me, True, True), (pda, False, True), (b58decode(SYSTEM_PROGRAM), False, False)], b"\x01" + bytes(8))
    raw, sig = build_tx(w.key, [ix], b58encode(bytes([9]) * 32))
    msg = raw[65:]
    w.key.verify_key.verify(msg, b58decode(sig))
    assert msg[:3] == bytes([1, 0, 2])  # payer signs; system + program are readonly
    keys = [msg[4 + 32 * i: 36 + 32 * i] for i in range(msg[3])]
    assert keys[0] == me and keys[1] == pda and set(keys[2:]) == {b58decode(SYSTEM_PROGRAM), c.program_id}


def test_parse_escrow_layout():
    data = (bytes([1, 1, 254]) + bytes([3]) * 32 + bytes([4]) * 32 + (5000).to_bytes(8, "little")
            + bytes([6]) * 32 + (-1).to_bytes(8, "little", signed=True))
    e = parse_escrow(data)
    assert e["state"] == "FUNDED" and e["amount"] == "5000" and e["deadline"] == -1
    assert e["payer"] == b58encode(bytes([3]) * 32) and e["agreement_hash"] == "06" * 32
