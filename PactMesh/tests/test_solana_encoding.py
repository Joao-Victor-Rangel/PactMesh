from nacl.signing import SigningKey

from pactmesh.ledger.solana import MEMO_PROGRAM, b58decode, b58encode, build_memo_tx, compact_u16, memo_text


def test_base58_roundtrip_and_known_program_id():
    assert len(b58decode(MEMO_PROGRAM)) == 32
    assert b58encode(b58decode(MEMO_PROGRAM)) == MEMO_PROGRAM
    assert b58encode(b"\0\0\x01") == "112"
    assert b58encode(bytes(32)) == "1" * 32  # system program id


def test_compact_u16():
    assert compact_u16(0) == b"\x00"
    assert compact_u16(127) == b"\x7f"
    assert compact_u16(128) == b"\x80\x01"
    assert compact_u16(16384) == b"\x80\x80\x01"


def test_memo_tx_layout_and_signature():
    key = SigningKey(bytes(range(32)))
    memo = memo_text("a" * 32, "b" * 64, "pactmesh-merkle/1")
    tx = build_memo_tx(key, memo, b58encode(bytes([7]) * 32))
    assert tx[0] == 1
    sig, message = tx[1:65], tx[65:]
    key.verify_key.verify(message, sig)
    assert message[:3] == bytes([1, 0, 1])
    assert message[3] == 2
    assert message[4:36] == key.verify_key.encode()
    assert message[36:68] == b58decode(MEMO_PROGRAM)
    assert message[68:100] == bytes([7]) * 32
    assert message.endswith(memo.encode())
