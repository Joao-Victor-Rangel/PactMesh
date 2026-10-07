# Market simulation

Seed 23: 6 Cripto buyers (36 tasks), 8 suppliers, 280 simulated seconds, 33.9 s wall time. Real crypto, policy, relays, runtimes and SIMULATED ledger; only the clock is simulated.

**ALL INVARIANTS HOLD**

| Invariant | Result | Detail |
|---|---|---|
| money conservation | PASS | CRPT-TEST in wallets + escrows: start 14000, end 14000 |
| no buyer exceeds its budget | PASS | max committed 388 of 400 per buyer |
| no contract above the task budget | PASS | 0 contracts above 100 |
| no double payment | PASS | 18 releases, each escrow released at most once |
| bad deliveries are disputed, never paid | PASS | 12 bad deliveries -> 12 disputed, 0 paid |
| silent suppliers are refunded after the deadline | PASS | 6 silent -> 6 refunded |
| greedy/injector suppliers never paid above budget | PASS | 0 paid |
| every settled receipt verifies | PASS | 18/18 receipts valid |
| every tampered receipt is detected | PASS | 18/18 tampered copies rejected |
| relays never see plaintext or buyer identities | PASS | markers found: none |
| replayed messages are detected and produce no new effects | PASS | 172 replayed envelopes posted; 874 duplicates dropped at ingest, 0 rejected as REPLAY |
| no buyer contracts again with a supplier it has evidence against | PASS | 0 repeat contracts; 24 later tasks excluded suppliers using their own evidence |
| liveness: every task reached a terminal state | PASS | 0 stuck |
| buyer crash + restart: its tasks still finished | PASS | buyer0 tasks: ['EXPIRED', 'DISPUTED', 'DISPUTED', 'SETTLED', 'SETTLED', 'SETTLED'] |

## Suppliers

| Supplier | Kind | List price | Floor |
|---|---|---|---|
| s0-honest | honest_cheap | 92 | 82 |
| s1-premium | honest_premium | 114 | 89 |
| s2-greedy | greedy | 171 | 166 |
| s3-injector | injector | 177 | 172 |
| s4-silent | silent | 76 | 70 |
| s5-bad_format | bad_format | 76 | 70 |
| s6-wrong_values | wrong_values | 78 | 72 |
| s7-honest | honest_cheap | 83 | 78 |

## Outcomes by the supplier that won the task

| Supplier kind | Outcomes |
|---|---|
| bad_format | DISPUTED 6 |
| honest_cheap | SETTLED 18 |
| silent | EXPIRED 6 |
| wrong_values | DISPUTED 6 |

## Chaos and attacks

| Tick (s) | Event | Detail |
|---|---|---|
| 15 | chaos | relay replica 0 DOWN |
| 30 | attack | replay attacker re-posted 40 captured envelopes |
| 40 | chaos | buyer0 process CRASHED and restarted from its vault (tasks: ['FUNDED']) |
| 70 | chaos | relay replica 0 back UP (same storage) |
| 90 | attack | replay attacker re-posted 80 captured envelopes |
| 110 | chaos | ledger RPC DOWN |
| 135 | chaos | ledger RPC back UP |
| 160 | attack | replay attacker re-posted 52 captured envelopes |

## Contracts

| Buyer | Engine | Task | State | Winner | Kind | Price | Escrow | Policy blocks  Excluded by own evidence |
|---|---|---|---|---|---|---|---|---|---|
| buyer0 | reference | b0f1ee98b2 | EXPIRED | s4-silent | silent | 76 | REFUNDED | - | - |
| buyer0 | reference | a5afa20050 | DISPUTED | s5-bad_format | bad_format | 76 | DISPUTED | EXPOSURE_LIMIT | - |
| buyer0 | reference | 7c6cc05de5 | DISPUTED | s6-wrong_values | wrong_values | 78 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer0 | reference | c112a301c2 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 82d0d43753 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer0 | reference | 65e2d5e35f | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | e2d4ca74ee | EXPIRED | s4-silent | silent | 76 | REFUNDED | - | - |
| buyer1 | simulated-llm | a382dbc4f0 | DISPUTED | s5-bad_format | bad_format | 76 | DISPUTED | EXPOSURE_LIMIT | - |
| buyer1 | simulated-llm | e83536b869 | DISPUTED | s6-wrong_values | wrong_values | 78 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer1 | simulated-llm | d341659577 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 7c4389e012 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 1e6105e985 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer2 | reference | d589663421 | EXPIRED | s4-silent | silent | 76 | REFUNDED | - | - |
| buyer2 | reference | 0228de854e | DISPUTED | s5-bad_format | bad_format | 76 | DISPUTED | EXPOSURE_LIMIT | - |
| buyer2 | reference | 508ad9ff2d | DISPUTED | s6-wrong_values | wrong_values | 78 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer2 | reference | 902a5062e8 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | 8fce432633 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer2 | reference | c23001e705 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | e29b98c2f2 | EXPIRED | s4-silent | silent | 76 | REFUNDED | - | - |
| buyer3 | simulated-llm | 44edaf6808 | DISPUTED | s5-bad_format | bad_format | 76 | DISPUTED | EXPOSURE_LIMIT | - |
| buyer3 | simulated-llm | 3e4511074f | DISPUTED | s6-wrong_values | wrong_values | 78 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer3 | simulated-llm | e2dc420ccf | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 367c4a5551 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | f6dd0a2822 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer4 | reference | e6988a751f | EXPIRED | s4-silent | silent | 76 | REFUNDED | - | - |
| buyer4 | reference | 02e2e79592 | DISPUTED | s5-bad_format | bad_format | 76 | DISPUTED | EXPOSURE_LIMIT | - |
| buyer4 | reference | e0d43e3535 | DISPUTED | s6-wrong_values | wrong_values | 78 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer4 | reference | 1ac94b5883 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | b1fb93ab1e | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer4 | reference | cb8bfb7d79 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | de31a475da | EXPIRED | s4-silent | silent | 76 | REFUNDED | - | - |
| buyer5 | simulated-llm | 4baf514553 | DISPUTED | s5-bad_format | bad_format | 76 | DISPUTED | EXPOSURE_LIMIT | - |
| buyer5 | simulated-llm | a9a6d4173a | DISPUTED | s6-wrong_values | wrong_values | 78 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer5 | simulated-llm | 75c1563cbe | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 9382434145 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | b60a98d269 | SETTLED | s7-honest | honest_cheap | 78 | RELEASED | - | s4-silent, s5-bad_format, s6-wrong_values |
