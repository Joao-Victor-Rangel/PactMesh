"""Cripto: the PactMesh agent runtime, shared by the buyer and supplier roles.

* persisted outbox with bounded retransmission (exponential backoff + jitter)
* persisted inbox with unique message_id (duplicate detection)
* per-session monotonic sequence and previous_hash continuity
* signed hash-chained event log, Merkle batches and anchoring
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from .canonical import hash_obj
from .crypto import EnvelopeError, Identity, mailbox_route, open_envelope, random_id, seal
from .evidence import MERKLE_VERSION, inclusion_proof, merkle_root
from .ledger import LedgerError, SimLedgerClient, Wallet
from .protocol import ProtocolError, build_message, message_hash, validate_message
from .store import Store
from .transport import TransportUnavailable, backoff
from .util import now, parse_iso

MAX_RETRANSMISSIONS = 3
log = logging.getLogger("pactmesh")


class Retry(Exception):
    """Transient condition: keep the inbox item and try again later."""


class Agent:
    role = "agent"

    def __init__(self, home: Path, name: str, transport, ledger: SimLedgerClient | None):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.store = Store(self.home / "vault.sqlite")
        self.identity = Identity.load_or_create(self.home / "identity.json")
        self.wallet = Wallet.load_or_create(self.home / "wallet.json")  # payment key, separate file
        self.transport = transport
        self.ledger = ledger
        if ledger is not None:
            ledger.wallet = self.wallet
        self.stop_event = threading.Event()
        self.metrics = {"retransmissions": 0, "rejected_messages": 0, "duplicates": 0}

    # ------------------------------------------------------------- routes

    def mailboxes(self) -> list[str]:
        """Secrets of the mailboxes this agent reads (never leave the vault)."""
        return self.store.get_kv("mailboxes", [])

    def new_mailbox(self) -> str:
        """Create a mailbox; returns its public deposit route."""
        secret = random_id()
        self.store.set_kv("mailboxes", self.mailboxes() + [secret])
        return mailbox_route(secret)

    # ------------------------------------------------------------- events

    def event(self, negotiation_id: str | None, etype: str, **body) -> dict:
        return self.store.append_event(self.identity, negotiation_id, etype, body)

    # ------------------------------------------------------------ sending

    def send(self, negotiation_id: str, peer: dict, session_id: str, type: str, payload: dict,
             reply_to: str | None = None, ttl: int = 120) -> dict:
        """Build, sign and enqueue a message (call inside a store.tx())."""
        with self.store.tx() as c:
            row = c.execute("SELECT last_seq, last_hash FROM seqs WHERE session_id=? AND direction='out'",
                            (session_id,)).fetchone()
            seq = row["last_seq"] + 1 if row else 0
            msg = build_message(self.identity, type=type, session_id=session_id, sequence=seq, payload=payload,
                                reply_to=reply_to, previous_hash=row["last_hash"] if row else None, ttl=ttl)
            c.execute("INSERT OR REPLACE INTO seqs (session_id, direction, key_id, last_seq, last_hash)"
                      " VALUES (?, 'out', ?, ?, ?)", (session_id, self.identity.key_id, seq, message_hash(msg)))
            c.execute("INSERT INTO outbox (message_id, negotiation_id, route, enc_key, msg, next_attempt_at)"
                      " VALUES (?,?,?,?,?,?)",
                      (msg["message_id"], negotiation_id, peer["route"], peer["enc_key"], json.dumps(msg), now()))
            self.event(negotiation_id, "MESSAGE_SENT", message_type=type, message_id=msg["message_id"],
                       session_id=session_id, message_hash=message_hash(msg))
        return msg

    def flush_outbox(self) -> None:
        rows = self.store.q("SELECT * FROM outbox WHERE status='pending' AND next_attempt_at<=? ORDER BY id",
                            (now(),))
        blocked_routes: set[str] = set()
        for r in rows:
            if r["route"] in blocked_routes:  # keep per-peer ordering
                continue
            msg = json.loads(r["msg"])
            if parse_iso(msg["expires_at"]) < now():
                self.store.q("UPDATE outbox SET status='expired' WHERE id=?", (r["id"],))
                continue
            try:
                env = seal(json.dumps(msg).encode(), r["enc_key"], r["route"], parse_iso(msg["expires_at"]))
                self.transport.send(env)
                self.store.q("UPDATE outbox SET status='sent', attempts=attempts+1 WHERE id=?", (r["id"],))
            except (TransportUnavailable, OSError) as e:
                blocked_routes.add(r["route"])
                attempts = r["attempts"] + 1
                if attempts > 0:
                    self.metrics["retransmissions"] += 1
                status = "failed" if attempts > MAX_RETRANSMISSIONS else "pending"
                self.store.q("UPDATE outbox SET attempts=?, status=?, last_error=?, next_attempt_at=? WHERE id=?",
                             (attempts, status, str(e)[:200], now() + int(backoff(attempts)) + 1, r["id"]))

    # ---------------------------------------------------------- receiving

    def poll(self) -> None:
        for secret in self.mailboxes():
            try:
                items = self.transport.receive(secret)
            except TransportUnavailable as e:
                log.warning("%s: transport unavailable: %s", self.name, e)
                return
            acks: dict[str, list[str]] = {}
            for relay, env in items:
                acks.setdefault(relay, []).append(env["id"])
                self._ingest(env)
            for relay, ids in acks.items():
                try:
                    self.transport.acknowledge(relay, secret, ids)
                except (TransportUnavailable, OSError):
                    pass
        self.process_inbox()

    def _ingest(self, env: dict) -> None:
        try:
            msg = validate_message(json.loads(open_envelope(env, self.identity)))
        except (EnvelopeError, ProtocolError, ValueError) as e:
            self.metrics["rejected_messages"] += 1
            code = getattr(e, "code", "UNDECRYPTABLE")
            self.event(None, "MESSAGE_REJECTED", code=code)
            return
        with self.store.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO inbox (message_id, session_id, type, msg, received_at)"
                            " VALUES (?,?,?,?,?)",
                            (msg["message_id"], msg["session_id"], msg["type"], json.dumps(msg), now()))
            if cur.rowcount == 0:
                self.metrics["duplicates"] += 1

    def ingest_message(self, msg: dict) -> None:
        """Entry point used by tests to inject an already-decrypted message."""
        msg = validate_message(msg)
        with self.store.tx() as c:
            c.execute("INSERT OR IGNORE INTO inbox (message_id, session_id, type, msg, received_at)"
                      " VALUES (?,?,?,?,?)", (msg["message_id"], msg["session_id"], msg["type"], json.dumps(msg), now()))

    def _check_sequence(self, msg: dict) -> None:
        row = self.store.q("SELECT key_id, last_seq, last_hash FROM seqs WHERE session_id=? AND direction='in'",
                           (msg["session_id"],))
        if row:
            r = row[0]
            if r["key_id"] != msg["sender_key_id"]:
                raise ProtocolError("SENDER_MISMATCH")
            if msg["sequence"] <= r["last_seq"]:
                raise ProtocolError("REPLAY")
            if msg["sequence"] == r["last_seq"] + 1 and msg["previous_hash"] != r["last_hash"]:
                raise ProtocolError("CHAIN_BROKEN")

    def _advance_sequence(self, msg: dict) -> None:
        self.store.q("INSERT OR REPLACE INTO seqs (session_id, direction, key_id, last_seq, last_hash)"
                     " VALUES (?, 'in', ?, ?, ?)",
                     (msg["session_id"], msg["sender_key_id"], msg["sequence"], message_hash(msg)))

    def process_inbox(self) -> None:
        rows = self.store.q("SELECT message_id, msg FROM inbox WHERE processed=0 ORDER BY received_at, rowid")
        for r in rows:
            msg = json.loads(r["msg"])
            if parse_iso(msg["expires_at"]) < now():
                self._finish(r["message_id"], "EXPIRED")
                continue
            try:
                with self.store.tx():
                    self._check_sequence(msg)
                    self.handle(msg)
                    self._advance_sequence(msg)
                    self.store.q("UPDATE inbox SET processed=1, result='OK' WHERE message_id=?", (r["message_id"],))
            except Retry:
                continue
            except ProtocolError as e:
                self.metrics["rejected_messages"] += 1
                self._finish(r["message_id"], e.code)
                self.event(None, "MESSAGE_REJECTED", code=e.code, message_type=msg["type"])

    def _finish(self, message_id: str, result: str) -> None:
        self.store.q("UPDATE inbox SET processed=1, result=? WHERE message_id=?", (result, message_id))

    def handle(self, msg: dict) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def tick(self) -> None:
        pass

    # ------------------------------------------------------------ evidence

    def make_batch(self) -> dict | None:
        """Commit all unbatched events into a Merkle batch and anchor it."""
        rows = self.store.q("SELECT seq, commitment FROM events WHERE batch_id IS NULL ORDER BY seq")
        if not rows:
            return None
        batch_id = random_id()
        commitments = [r["commitment"] for r in rows]
        root = merkle_root(commitments)
        with self.store.tx() as c:
            c.execute("INSERT INTO batches (batch_id, root, size, version, created_at) VALUES (?,?,?,?,?)",
                      (batch_id, root, len(rows), MERKLE_VERSION, now()))
            c.executemany("UPDATE events SET batch_id=? WHERE seq=?", [(batch_id, r["seq"]) for r in rows])
        self.anchor_batch(batch_id)
        return self.batch(batch_id)

    def anchor_batch(self, batch_id: str) -> None:
        b = self.batch(batch_id)
        if b["anchor"] or self.ledger is None:
            return
        try:
            st = self.ledger.anchor(batch_id, b["root"], b["version"])
            anchor = {"network": self.ledger.network, "simulated": self.ledger.simulated,
                      "tx": st["signature"], "ok": st["ok"], "slot": st["slot"]}
            self.store.q("UPDATE batches SET anchor=? WHERE batch_id=?", (json.dumps(anchor), batch_id))
        except LedgerError as e:
            log.warning("%s: anchoring pending (%s)", self.name, e)

    def batch(self, batch_id: str) -> dict | None:
        rows = self.store.q("SELECT * FROM batches WHERE batch_id=?", (batch_id,))
        if not rows:
            return None
        r = rows[0]
        return {"batch_id": r["batch_id"], "root": r["root"], "size": r["size"], "version": r["version"],
                "anchor": json.loads(r["anchor"]) if r["anchor"] else None}

    def disclose(self, negotiation_id: str) -> list[dict]:
        """Selective disclosure: events of one negotiation with randomness
        and inclusion proofs; other events stay private."""
        out = []
        for ev in self.store.events(negotiation_id):
            if not ev["batch_id"]:
                continue
            batch_rows = self.store.q("SELECT commitment FROM events WHERE batch_id=? ORDER BY seq", (ev["batch_id"],))
            comms = [x["commitment"] for x in batch_rows]
            out.append({**ev, "proof": inclusion_proof(comms, comms.index(ev["commitment"]))})
        return out

    # ---------------------------------------------------------------- loop

    def step(self) -> None:
        self.poll()
        self.tick()
        self.flush_outbox()

    def run(self, interval: float = 0.25) -> None:
        while not self.stop_event.is_set():
            try:
                self.step()
            except Exception:  # keep the agent alive; state is persisted
                log.exception("%s: step failed", self.name)
            time.sleep(interval)


def agreement_signature_ok(agreement_hash_hex: str, key_id: str, sig: str | None) -> bool:
    from .crypto import verify_sig

    return bool(sig) and verify_sig(key_id, bytes.fromhex(agreement_hash_hex), sig)


__all__ = ["Agent", "Retry", "agreement_signature_ok", "hash_obj"]
