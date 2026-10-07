"""Key management, signatures and envelope encryption.

Only high level libsodium primitives (via PyNaCl) are used:

* Ed25519 for message, agreement and event signatures.
* crypto_kx (X25519 + BLAKE2b, binds both public keys) with a fresh
  ephemeral key per envelope to derive the envelope key.
* XChaCha20-Poly1305 IETF AEAD for envelopes; the outer header is
  authenticated as associated data.
* secretstream (XChaCha20-Poly1305) for segmented artifacts, which also
  authenticates stream termination.

Message keys and payment (wallet) keys are separate; this module never
touches payment keys.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

import nacl.bindings as sodium
from nacl.exceptions import BadSignatureError, CryptoError
from nacl.signing import SigningKey, VerifyKey

from .canonical import canonical

TRANSPORT_VERSION = "pactmesh-transport/0.1"
SIZE_CLASSES = (1024, 4096, 16384, 65536)
MAX_CONTROL_MESSAGE = SIZE_CLASSES[-1]
ARTIFACT_CHUNK = 16384


class EnvelopeError(ValueError):
    pass


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode(), validate=True)


def mailbox_route(secret_hex: str) -> str:
    """Public deposit route of a mailbox. Reading or acknowledging requires
    the secret itself, so publishing the route (e.g. in an advert) does not
    let anyone read or delete the mailbox's messages."""
    import hashlib

    return hashlib.sha256(b"pactmesh/mailbox/v1" + bytes.fromhex(secret_hex)).hexdigest()[:32]


def random_id(nbytes: int = 16) -> str:
    """Random identifier (128 bits by default) that carries no user data."""
    return secrets.token_hex(nbytes)


@dataclass
class Identity:
    """Message identity of one agent: Ed25519 signing key + X25519 kx key."""

    signing_key: SigningKey
    kx_public: bytes
    kx_secret: bytes

    @classmethod
    def generate(cls) -> "Identity":
        pk, sk = sodium.crypto_kx_keypair()
        return cls(SigningKey.generate(), pk, sk)

    @classmethod
    def load_or_create(cls, path: Path) -> "Identity":
        if path.exists():
            data = json.loads(path.read_text())
            pk, sk = sodium.crypto_kx_seed_keypair(bytes.fromhex(data["kx_seed"]))
            return cls(SigningKey(bytes.fromhex(data["signing_seed"])), pk, sk)
        signing_seed = os.urandom(32)
        kx_seed = os.urandom(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"signing_seed": signing_seed.hex(), "kx_seed": kx_seed.hex()}, f)
        pk, sk = sodium.crypto_kx_seed_keypair(kx_seed)
        return cls(SigningKey(signing_seed), pk, sk)

    @property
    def key_id(self) -> str:
        return self.signing_key.verify_key.encode().hex()

    @property
    def enc_public_b64(self) -> str:
        return b64e(self.kx_public)

    def sign(self, data: bytes) -> str:
        return self.signing_key.sign(data).signature.hex()

    def sign_obj(self, obj: dict, field: str = "signature") -> dict:
        body = {k: v for k, v in obj.items() if k != field}
        return {**body, field: self.sign(canonical(body))}


def verify_sig(key_id: str, data: bytes, signature_hex: str) -> bool:
    try:
        VerifyKey(bytes.fromhex(key_id)).verify(data, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def verify_obj(obj: dict, key_id: str, field: str = "signature") -> bool:
    sig = obj.get(field)
    if not isinstance(sig, str):
        return False
    body = {k: v for k, v in obj.items() if k != field}
    return verify_sig(key_id, canonical(body), sig)


# ---------------------------------------------------------------- envelopes


def _pad(plaintext: bytes) -> tuple[bytes, int]:
    needed = len(plaintext) + 4
    for size in SIZE_CLASSES:
        if needed <= size:
            return struct.pack(">I", len(plaintext)) + plaintext + bytes(size - needed), size
    raise EnvelopeError(f"message exceeds {MAX_CONTROL_MESSAGE} bytes")


def _unpad(padded: bytes) -> bytes:
    (n,) = struct.unpack(">I", padded[:4])
    if n > len(padded) - 4:
        raise EnvelopeError("bad padding")
    return padded[4 : 4 + n]


def seal(plaintext: bytes, recipient_enc_pub_b64: str, route: str, expires_at: int) -> dict:
    """Encrypt an inner message into an opaque outer envelope.

    The outer header only carries what a relay needs: version, random
    message id, mailbox route, coarse expiry and size class. Identities,
    prices and session ids live exclusively inside the ciphertext.
    """
    padded, size_class = _pad(plaintext)
    eph_pk, eph_sk = sodium.crypto_kx_keypair()
    _rx, key = sodium.crypto_kx_client_session_keys(eph_pk, eph_sk, b64d(recipient_enc_pub_b64))
    header = {
        "v": TRANSPORT_VERSION,
        "id": random_id(),
        "route": route,
        "exp": expires_at - (expires_at % 60) + 60,  # coarse, rounded up to the minute
        "size": size_class,
        "eph": b64e(eph_pk),
        "nonce": b64e(os.urandom(sodium.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES)),
    }
    ct = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
        padded, canonical(header), b64d(header["nonce"]), key
    )
    return {**header, "ct": b64e(ct)}


def open_envelope(envelope: dict, identity: Identity) -> bytes:
    try:
        header = {k: v for k, v in envelope.items() if k != "ct"}
        if header.get("v") != TRANSPORT_VERSION:
            raise EnvelopeError("unknown transport version")
        key, _tx = sodium.crypto_kx_server_session_keys(
            identity.kx_public, identity.kx_secret, b64d(header["eph"])
        )
        padded = sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
            b64d(envelope["ct"]), canonical(header), b64d(header["nonce"]), key
        )
    except (KeyError, TypeError, ValueError, CryptoError) as e:
        if isinstance(e, EnvelopeError):
            raise
        raise EnvelopeError(f"cannot open envelope: {e}") from e
    if len(padded) != header.get("size"):
        raise EnvelopeError("size class mismatch")
    return _unpad(padded)


# ---------------------------------------------------------------- artifacts


def encrypt_artifact(data: bytes) -> tuple[bytes, dict]:
    """Encrypt a large artifact with secretstream. Returns blob and the
    access descriptor (key + header) that travels inside an envelope."""
    key = sodium.crypto_secretstream_xchacha20poly1305_keygen()
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    header = sodium.crypto_secretstream_xchacha20poly1305_init_push(state, key)
    chunks = [data[i : i + ARTIFACT_CHUNK] for i in range(0, len(data), ARTIFACT_CHUNK)] or [b""]
    out = bytearray()
    for i, chunk in enumerate(chunks):
        tag = (
            sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
            if i == len(chunks) - 1
            else sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
        )
        c = sodium.crypto_secretstream_xchacha20poly1305_push(state, chunk, None, tag)
        out += struct.pack(">I", len(c)) + c
    return bytes(out), {"key": b64e(key), "header": b64e(header), "chunk": ARTIFACT_CHUNK}


def decrypt_artifact(blob: bytes, access: dict) -> bytes:
    state = sodium.crypto_secretstream_xchacha20poly1305_state()
    sodium.crypto_secretstream_xchacha20poly1305_init_pull(
        state, b64d(access["header"]), b64d(access["key"])
    )
    out = bytearray()
    pos, final = 0, False
    try:
        while pos < len(blob):
            if final:
                raise EnvelopeError("data after final segment")
            (n,) = struct.unpack(">I", blob[pos : pos + 4])
            c = blob[pos + 4 : pos + 4 + n]
            if len(c) != n:
                raise EnvelopeError("truncated segment")
            pos += 4 + n
            m, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(state, c, None)
            out += m
            final = tag == sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
    except (CryptoError, struct.error) as e:
        raise EnvelopeError(f"artifact corrupted: {e}") from e
    if not final:
        raise EnvelopeError("artifact stream truncated (no final tag)")
    return bytes(out)
