"""Local vault: one SQLite database per participant.

Every state transition is written in the same transaction as its signed
event and its outbox items, so a crash never leaves a transition without
the messages it must send (or vice versa). The inbox has a unique
constraint on message_id for duplicate detection; financial effects have a
unique (negotiation, action) key so they can never be applied twice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .canonical import hash_obj
from .evidence import commitment, new_randomness
from .util import iso, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS negotiations (
  id TEXT PRIMARY KEY, role TEXT NOT NULL, state TEXT NOT NULL,
  data TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY, negotiation_id TEXT, event TEXT NOT NULL,
  event_hash TEXT NOT NULL, signature TEXT NOT NULL, randomness TEXT NOT NULL,
  commitment TEXT NOT NULL, batch_id TEXT);
CREATE TABLE IF NOT EXISTS inbox (
  message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
  msg TEXT NOT NULL, received_at INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
  result TEXT);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL,
  negotiation_id TEXT, route TEXT NOT NULL, enc_key TEXT NOT NULL, msg TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', last_error TEXT);
CREATE TABLE IF NOT EXISTS seqs (
  session_id TEXT NOT NULL, direction TEXT NOT NULL, key_id TEXT NOT NULL,
  last_seq INTEGER NOT NULL, last_hash TEXT, PRIMARY KEY (session_id, direction));
CREATE TABLE IF NOT EXISTS reservations (
  negotiation_id TEXT PRIMARY KEY, amount INTEGER NOT NULL, status TEXT NOT NULL,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS effects (
  negotiation_id TEXT NOT NULL, action TEXT NOT NULL, result TEXT NOT NULL,
  created_at INTEGER NOT NULL, PRIMARY KEY (negotiation_id, action));
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, root TEXT NOT NULL, size INTEGER NOT NULL,
  version TEXT NOT NULL, anchor TEXT, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS records (
  kind TEXT NOT NULL, id TEXT NOT NULL, negotiation_id TEXT, data TEXT NOT NULL,
  created_at INTEGER NOT NULL, PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS idempotency (k TEXT PRIMARY KEY, response TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._depth = 0

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self.conn
                finally:
                    self._depth -= 1
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    # --------------------------------------------------------- kv / records

    def get_kv(self, k: str, default: Any = None) -> Any:
        rows = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(rows[0]["v"]) if rows else default

    def set_kv(self, k: str, v: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, json.dumps(v)))

    def put_record(self, kind: str, id: str, data: dict, negotiation_id: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO records (kind, id, negotiation_id, data, created_at) VALUES (?,?,?,?,?)",
                (kind, id, negotiation_id, json.dumps(data), now()),
            )

    def get_record(self, kind: str, id: str) -> dict | None:
        rows = self.q("SELECT data FROM records WHERE kind=? AND id=?", (kind, id))
        return json.loads(rows[0]["data"]) if rows else None

    def records(self, kind: str, negotiation_id: str | None = None) -> list[dict]:
        if negotiation_id is None:
            rows = self.q("SELECT data FROM records WHERE kind=? ORDER BY created_at, rowid", (kind,))
        else:
            rows = self.q(
                "SELECT data FROM records WHERE kind=? AND negotiation_id=? ORDER BY created_at, rowid",
                (kind, negotiation_id),
            )
        return [json.loads(r["data"]) for r in rows]

    # --------------------------------------------------------- negotiations

    def create_negotiation(self, id: str, role: str, state: str, data: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO negotiations (id, role, state, data, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (id, role, state, json.dumps(data), now(), now()),
            )

    def get_negotiation(self, id: str) -> dict | None:
        rows = self.q("SELECT * FROM negotiations WHERE id=?", (id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "role": r["role"], "state": r["state"], "data": json.loads(r["data"]),
                "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def list_negotiations(self, states: tuple[str, ...] | None = None) -> list[dict]:
        rows = self.q("SELECT id FROM negotiations ORDER BY created_at, rowid")
        out = [self.get_negotiation(r["id"]) for r in rows]
        return [n for n in out if n and (states is None or n["state"] in states)]

    def save_negotiation(self, id: str, state: str, data: dict) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE negotiations SET state=?, data=?, updated_at=? WHERE id=?",
                (state, json.dumps(data), now(), id),
            )

    # --------------------------------------------------------------- events

    def append_event(self, identity, negotiation_id: str | None, etype: str, body: dict) -> dict:
        """Append a signed, hash-chained event and its hiding commitment."""
        with self.tx() as c:
            last = c.execute("SELECT seq, event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            seq = (last["seq"] + 1) if last else 1
            event = {
                "seq": seq,
                "agent_key_id": identity.key_id,
                "negotiation_id": negotiation_id,
                "type": etype,
                "at": iso(now()),
                "prev_hash": last["event_hash"] if last else None,
                **body,
            }
            event_hash = hash_obj(event)
            sig = identity.sign(bytes.fromhex(event_hash))
            r = new_randomness()
            c.execute(
                "INSERT INTO events (seq, negotiation_id, event, event_hash, signature, randomness, commitment)"
                " VALUES (?,?,?,?,?,?,?)",
                (seq, negotiation_id, json.dumps(event), event_hash, sig, r.hex(), commitment(event, r)),
            )
            return event

    def events(self, negotiation_id: str | None = None) -> list[dict]:
        if negotiation_id is None:
            rows = self.q("SELECT * FROM events ORDER BY seq")
        else:
            rows = self.q("SELECT * FROM events WHERE negotiation_id=? ORDER BY seq", (negotiation_id,))
        return [
            {"event": json.loads(r["event"]), "event_hash": r["event_hash"], "signature": r["signature"],
             "randomness": r["randomness"], "commitment": r["commitment"], "batch_id": r["batch_id"]}
            for r in rows
        ]

    # ---------------------------------------------------------- reservations

    def reserve(self, negotiation_id: str, amount: int, budget: int) -> bool:
        """Atomically reserve budget. Concurrent negotiations cannot both
        consume the same balance because BEGIN IMMEDIATE serializes writers."""
        with self.tx() as c:
            existing = c.execute(
                "SELECT amount, status FROM reservations WHERE negotiation_id=?", (negotiation_id,)
            ).fetchone()
            if existing and existing["status"] in ("active", "consumed"):
                return existing["amount"] == amount
            used = c.execute(
                "SELECT COALESCE(SUM(amount),0) s FROM reservations WHERE status IN ('active','consumed')"
            ).fetchone()["s"]
            if used + amount > budget:
                return False
            c.execute(
                "INSERT OR REPLACE INTO reservations (negotiation_id, amount, status, created_at) VALUES (?,?,?,?)",
                (negotiation_id, amount, "active", now()),
            )
            return True

    def set_reservation(self, negotiation_id: str, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE reservations SET status=? WHERE negotiation_id=?", (status, negotiation_id))

    def committed_spend(self) -> int:
        return self.q(
            "SELECT COALESCE(SUM(amount),0) s FROM reservations WHERE status IN ('active','consumed')"
        )[0]["s"]

    # --------------------------------------------------------------- effects

    def get_effect(self, negotiation_id: str, action: str) -> dict | None:
        rows = self.q("SELECT result FROM effects WHERE negotiation_id=? AND action=?", (negotiation_id, action))
        return json.loads(rows[0]["result"]) if rows else None

    def record_effect(self, negotiation_id: str, action: str, result: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO effects (negotiation_id, action, result, created_at) VALUES (?,?,?,?)",
                (negotiation_id, action, json.dumps(result), now()),
            )
