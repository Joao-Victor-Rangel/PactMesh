"""Local administrative API + dashboard for the buyer (127.0.0.1 only by default).

Agent-to-agent traffic never goes through this API. Mutating operations
require the bearer token AND an Idempotency-Key header.
"""

from __future__ import annotations

import hmac
import json
from pathlib import Path

from .audit import verify_package
from .httpbase import App, HttpError, Raw, Request

DASHBOARD = Path(__file__).with_name("dashboard.html")


def build_api(buyer, token: str, default_dataset: bytes | None = None) -> App:
    app = App()

    def auth(req: Request) -> None:
        h = req.header("authorization") or ""
        if not hmac.compare_digest(h.encode(), f"Bearer {token}".encode()):
            raise HttpError(401, "UNAUTHORIZED")

    def idempotent(req: Request, fn):
        key = req.header("idempotency-key")
        if not key or len(key) > 128:
            raise HttpError(400, "IDEMPOTENCY_KEY_REQUIRED")
        rows = buyer.store.q("SELECT response FROM idempotency WHERE k=?", (f"{req.path}:{key}",))
        if rows:
            return json.loads(rows[0]["response"])
        out = fn()
        buyer.store.q("INSERT OR IGNORE INTO idempotency (k, response) VALUES (?, ?)",
                      (f"{req.path}:{key}", json.dumps(out)))
        return out

    @app.route("GET", "/")
    def index(req):
        return Raw(DASHBOARD.read_bytes(), "text/html; charset=utf-8")

    @app.route("GET", "/health")
    def health(req):
        return {"ok": True, "agent": buyer.name, "transport": buyer.transport.status(),
                "ledger": buyer.ledger.health(), "policy_hash": buyer.policy.hash, "paused": buyer.policy.paused,
                "decision_engine": buyer.engine.model_id}

    @app.route("POST", "/tasks")
    def create(req: Request):
        auth(req)
        body = req.json() or {}

        def go():
            csv_bytes = body["dataset_csv"].encode() if body.get("dataset_csv") else default_dataset
            if not csv_bytes:
                raise HttpError(400, "DATASET_REQUIRED")
            try:
                tid = buyer.create_task(csv_bytes=csv_bytes, column=str(body.get("column", "latency_ms")),
                                        budget=int(body.get("budget", 100)),
                                        quote_window_seconds=int(body.get("quote_window_seconds", 6)))
            except (ValueError, KeyError) as e:
                raise HttpError(400, "BAD_TASK", str(e))
            return {"task_id": tid}

        return idempotent(req, go)

    @app.route("GET", "/tasks")
    def list_tasks(req):
        auth(req)
        return {"tasks": [{"task_id": n["id"], "state": n["state"], "budget": n["data"]["task"]["budget"],
                           "created_at": n["created_at"], "needs_human": bool(n["data"].get("needs_human"))}
                          for n in buyer.store.list_negotiations()][::-1],
                "committed_spend": str(buyer.store.committed_spend()), "budget_total": buyer.policy.config["budget_total"]}

    @app.route("GET", "/tasks/([0-9a-f]{32})")
    def get_task(req, tid):
        auth(req)
        try:
            return buyer.summary(tid)
        except KeyError:
            raise HttpError(404, "NOT_FOUND")

    @app.route("POST", "/tasks/([0-9a-f]{32})/cancel")
    def cancel(req, tid):
        auth(req)

        def go():
            try:
                return buyer.cancel(tid)
            except KeyError:
                raise HttpError(404, "NOT_FOUND")

        return idempotent(req, go)

    @app.route("GET", "/negotiations/([0-9a-f]{32})/evidence")
    def evidence(req, tid):
        auth(req)
        try:
            return buyer.evidence_package(tid)
        except KeyError:
            raise HttpError(404, "NO_EVIDENCE_YET")

    @app.route("POST", "/verify")
    def verify(req: Request):
        auth(req)
        return verify_package(req.json() or {}, buyer.ledger)

    @app.route("POST", "/policy/pause")
    def pause(req: Request):
        auth(req)
        body = req.json() or {}

        def go():
            buyer.policy.set_paused(bool(body.get("paused", True)))
            buyer.event(None, "KILL_SWITCH", paused=buyer.policy.paused)
            return {"paused": buyer.policy.paused}

        return idempotent(req, go)

    @app.route("GET", "/metrics")
    def metrics(req):
        auth(req)
        q = buyer.store.q
        return {**buyer.metrics,
                "outbox_pending": q("SELECT COUNT(*) c FROM outbox WHERE status='pending'")[0]["c"],
                "outbox_failed": q("SELECT COUNT(*) c FROM outbox WHERE status='failed'")[0]["c"],
                "policy_blocks": sum(1 for r in buyer.store.records("policy_result") if not r["allowed"]),
                "batches": q("SELECT COUNT(*) c FROM batches")[0]["c"]}

    @app.route("GET", "/public")
    def public(req):
        # Aggregated view only: no conversations, no per-event timing.
        states: dict[str, int] = {}
        for n in buyer.store.list_negotiations():
            states[n["state"]] = states.get(n["state"], 0) + 1
        roots = [{"batch_id": r["batch_id"], "root": r["root"], "anchored": bool(r["anchor"])}
                 for r in buyer.store.q("SELECT batch_id, root, anchor FROM batches ORDER BY created_at")]
        return {"contracts_by_state": states, "evidence_batches": roots}

    return app
