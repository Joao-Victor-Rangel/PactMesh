# Market simulation

`python -m pactmesh simulate` runs a whole market of Cripto agents on the **real stack**: cryptography,
protocol, policy engine, buyer and supplier runtimes, two replicated relays over HTTP and the simulated
ledger. Only the clock is simulated (one second per tick), so deadlines, refunds and finality happen
quickly and deterministically.

## What happens in the default run (seed 7)

- **6 buyers**, half deciding with the reference rules and half with the gullible test model. Each has 6
  tasks spread over time (one every 45 s) and a total budget of 400 that cannot cover them all.
- **8 suppliers:** two honest and cheap, one honest premium, one greedy (always above budget), one prompt
  injector (above budget, with instructions hidden in its text), and three dishonest ones: one never
  delivers, one delivers an out-of-format report, one delivers wrong numbers.
- **Chaos and attacks on a schedule:** relay replica 0 goes down at 15 s and comes back at 70 s; buyer0's
  process crashes at 40 s and restarts from its vault; the ledger RPC is down from 110 s to 135 s; a
  replay attacker re-posts captured envelopes at 30, 90 and 160 s.

## Invariants checked at the end (the run fails if any is violated)

| Invariant | Why it matters |
|---|---|
| Money conservation (wallets + escrows) | nothing is created or lost |
| No buyer exceeds its total budget; no contract above the task budget | the policy holds under concurrency |
| No double payment | each escrow is released at most once |
| Bad deliveries are disputed, never paid | the verifier gates payment |
| Silent suppliers are refunded after the deadline | funds come back when work never arrives |
| Greedy and injector suppliers never paid above budget | prompt injection cannot move money |
| Every settled receipt verifies; every tampered copy is rejected | evidence is sound |
| Relays never see plaintext or buyer identities | privacy of content |
| Replayed messages are detected and produce no new effects | replay resistance |
| No buyer contracts again with a supplier it has evidence against | the agent learns from its own verified history |
| Liveness: every task reaches a terminal state | nothing gets stuck, even with outages |
| Buyer crash + restart: its tasks still finish | recovery from the vault |

## Two behaviours added because the simulation exposed them

1. **Verifiable local history** (spec section 6, "previous verifiable results"). Without it, buyers kept
   choosing the cheapest supplier, which was the dishonest one, task after task. Now a buyer excludes a
   supplier after a failed verification or a missing delivery recorded in its **own** evidence. There is
   no global reputation: the spec leaves that out of the MVP because of Sybil attacks.
2. **Exposure limit** (`max_open_contracts_per_supplier`, policy code `EXPOSURE_LIMIT`). A supplier that
   never delivers is only caught at the deadline; without a limit it could hold several of the same
   buyer's contracts by then. Now at most one funded, unverified contract per supplier per buyer.

The simulation also found a **runtime bug**: a ledger outage while a supplier was handling a message
raised instead of being retried, and could reorder messages of a session. Transient ledger errors are now
retried, and later messages of the same session wait (regression test included).

## Results

- One seed in full: [evaluation/market/results.md](../evaluation/market/results.md)
- Several seeds: [evaluation/market/seeds.md](../evaluation/market/seeds.md) (`python -m pactmesh simulate --seeds 7,11,23,42`)
- Larger market (12 buyers × 12 suppliers, 72 tasks): 36 settled, 12 disputed, 12 refunded, 12 cancelled
  by budget, all 14 invariants held (`python -m pactmesh simulate --buyers 12 --suppliers 12 --seed 5`).

The ledger is the SIMULATED one, and the network is localhost, so these runs show correctness under
concurrency, adversaries and failures. They do not show network latency or anonymity.
