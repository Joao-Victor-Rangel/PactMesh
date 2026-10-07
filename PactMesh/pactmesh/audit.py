"""Independent verification of an evidence package (auditor side).

Checks what the evidence can actually show: signatures, integrity,
inclusion of disclosed events in the anchored batch and the escrow state.
It does NOT prove that an event's content is true, that the model ran
correctly, or that the delivered service had good quality.
"""

from __future__ import annotations

from .canonical import hash_obj
from .crypto import verify_obj, verify_sig
from .evidence import commitment, verify_inclusion
from .protocol import agreement_hash


def verify_package(pkg: dict, ledger=None) -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    r = pkg.get("receipt", {})
    ag = r.get("agreement", {})
    check("receipt_signature", verify_obj(r, r.get("buyer_key_id", ""), "receipt_signature"),
          "receipt signed by buyer key")
    ah = agreement_hash(ag) if ag else ""
    check("agreement_hash", ah == r.get("agreement_hash"), "agreement content matches its hash")
    sigs = r.get("signatures", {})
    check("buyer_agreement_signature",
          bool(ah) and verify_sig(ag.get("buyer_key_id", ""), bytes.fromhex(ah), sigs.get("buyer", "")))
    check("supplier_agreement_signature",
          bool(ah) and verify_sig(ag.get("supplier_key_id", ""), bytes.fromhex(ah), sigs.get("supplier", "")))

    batch = r.get("evidence_batch", {})
    root = batch.get("root", "")
    events = pkg.get("disclosed_events", [])
    prev = None
    bad = []
    for e in events:
        ev = e["event"]
        eh = hash_obj(ev)
        if eh != e["event_hash"] or not verify_sig(ev["agent_key_id"], bytes.fromhex(eh), e["signature"]):
            bad.append(f"seq {ev.get('seq')}: signature/hash")
        if commitment(ev, bytes.fromhex(e["randomness"])) != e["commitment"]:
            bad.append(f"seq {ev.get('seq')}: commitment")
        if not verify_inclusion(e["commitment"], e["proof"], root):
            bad.append(f"seq {ev.get('seq')}: not included in batch root")
        if prev and ev.get("seq") == prev["seq"] + 1 and ev.get("prev_hash") != hash_obj(prev):
            bad.append(f"seq {ev.get('seq')}: chain broken")
        prev = ev
    check("disclosed_events", bool(events) and not bad, "; ".join(bad[:5]) or f"{len(events)} events verified")

    if ledger is not None:
        anchor_tx = (batch.get("anchor") or {}).get("tx")
        anchor = ledger.get_anchor(batch["batch_id"], anchor_tx) if batch.get("batch_id") else None
        check("batch_anchored", bool(anchor) and anchor.get("root") == root,
              f"anchored at slot {anchor.get('slot')}" if anchor else "anchor not found")
        esc = ledger.get_escrow(r.get("settlement", {}).get("escrow_id", ""))
        check("escrow_matches_agreement",
              bool(esc) and esc.get("agreement_hash") == ah and esc.get("amount") == ag.get("price")
              and esc.get("payee") == ag.get("payee"),
              f"escrow state {esc.get('state')}" if esc else "escrow not found")
        if r.get("status") == "ACCEPTED":
            check("escrow_released", bool(esc) and esc.get("state") == "RELEASED")
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "limits": "Proves signatures, integrity and inclusion; not truth of content or service quality."}
