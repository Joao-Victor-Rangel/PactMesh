# Cripito architecture, protocol and threat model

## Trust boundaries

- The **decision engine** gets structured state and a closed option list. It holds no keys, cannot call
  the ledger and cannot invent prices (counteroffers must come from the policy's grid).
- The **policy engine** is the user's explicit control. Its config is versioned and hashed. Every result
  (allowed or blocked) is signed, persisted and bound to `params_hash`, `policy_hash` and a 30 s validity.
  Executors call `check_authorization` immediately before signing anything.
- Only the **executor** path in `buyer.py` touches the wallet key, which lives in its own file
  (`wallet.json`), separate from the message identity (`identity.json`).
- **Relays** store opaque envelopes, signed adverts and encrypted blobs. They never get keys.
- Supplier text (`description`) is untrusted data. It is shown in the UI as such and never changes policy.

## Buyer state machine

```
CREATED -> QUOTING -> NEGOTIATING -> AGREED -> FUNDING_PENDING -> FUNDED -> DELIVERED -> VERIFIED
        -> SETTLEMENT_PENDING -> SETTLED
any pre-funding state -> CANCELLED | EXPIRED      FUNDED -> EXPIRED (silent supplier, refund after deadline)
DELIVERED -> DISPUTED (verifier failed: escrow frozen, evidence preserved, no release)
```

Transitions are enforced by `TRANSITIONS` in `buyer.py`; each is written in one SQLite transaction with
its signed event and outbox items. Financial effects (`ESCROW_CREATE`, `FUND_ESCROW`, `RELEASE_PAYMENT`,
`REFUND`, `DISPUTE`) have a unique key per negotiation, and every tick reconciles against the ledger
before acting, so a crash between "tx landed" and "effect recorded" never pays twice.

## Messages

Outer envelope (relay-visible): `v, id (random 128-bit), route (mailbox), exp (rounded to the minute),
size (class), eph, nonce, ct`. Everything else is inside `ct`.

Inner message: `protocol_version, message_id, session_id, sequence, type, created_at, expires_at,
sender_key_id, reply_to, previous_hash, payload, signature`.

Types: `SERVICE_ADVERT` (published to relay mirrors), `TASK_REQUEST`, `QUOTE`, `COUNTEROFFER`, `ACCEPT`
(buyer-signed agreement; supplier replies with its countersignature), `DECLINE`, `FUNDING_NOTICE`
(escrow id + encrypted dataset access), `DELIVERY`, `RECEIPT`, `CANCEL`, `ACK`, `ERROR`.

The accept points to the exact `terms_hash`; any change needs a new quote. Agreements are hashed with a
domain tag and signed by both parties over the hash.

Limits: 64 KiB control messages, 3 counter rounds, 3 retransmissions with exponential backoff + jitter.

## Evidence

`commitment = SHA256(lp(domain) || lp(r32) || lp(canonical(event)))`, with `lp` a 4-byte length prefix.
Merkle `cripito-merkle/1`: leaf `SHA256(0x00||c)`, node `SHA256(0x01||l||r)`, insertion order, unpaired
node promoted. Roots are anchored on the ledger (simulated, or Solana Devnet memo). The evidence package
discloses only one negotiation's events with their randomness and inclusion proofs.

## Threat model (summary)

| Adversary / failure | Mitigation | Limit |
|---|---|---|
| Curious relay | AEAD envelopes, padding classes, coarse expiry | sees mailbox, timing, IP |
| Traffic observer | mixnet mode: relay reached via Nym with SURB replies | direct mode gives none; global observer not claimed |
| Malicious supplier | minimal data, signed terms, verifier before release, escrow | can copy received data |
| Prompt injection | schema validation, closed actions, policy on effects | model can still be fooled; only recommendations |
| Replay | message ids, expiry, per-session sequence, persisted dedup | — |
| Stolen key | separate message/payment keys, kill switch | past signatures remain valid |
| Chain observer | batched commitments with hidden randomness | payments are public/pseudonymous |
| Fake adverts | signed, expiring adverts, optional allow-list | availability can be degraded |
