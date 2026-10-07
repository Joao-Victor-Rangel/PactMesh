# Market simulation

Seed 7: 6 Cripto buyers (36 tasks), 8 suppliers, 303 simulated seconds, 39.5 s wall time. Real crypto, policy, relays, runtimes and SIMULATED ledger; only the clock is simulated.

**ALL INVARIANTS HOLD**

| Invariant | Result | Detail |
|---|---|---|
| money conservation | PASS | CRPT-TEST in wallets + escrows: start 14000, end 14000 |
| no buyer exceeds its budget | PASS | max committed 305 of 400 per buyer |
| no contract above the task budget | PASS | 0 contracts above 100 |
| no double payment | PASS | 12 releases, each escrow released at most once |
| bad deliveries are disputed, never paid | PASS | 12 bad deliveries -> 12 disputed, 0 paid |
| silent suppliers are refunded after the deadline | PASS | 6 silent -> 6 refunded |
| greedy/injector suppliers never paid above budget | PASS | 0 paid |
| every settled receipt verifies | PASS | 12/12 receipts valid |
| every tampered receipt is detected | PASS | 12/12 tampered copies rejected |
| relays never see plaintext or buyer identities | PASS | markers found: none |
| replayed messages are detected and produce no new effects | PASS | 172 replayed envelopes posted; 891 duplicates dropped at ingest, 0 rejected as REPLAY |
| no buyer contracts again with a supplier it has evidence against | PASS | 0 repeat contracts; 30 later tasks excluded suppliers using their own evidence |
| liveness: every task reached a terminal state | PASS | 0 stuck |
| buyer crash + restart: its tasks still finished | PASS | buyer0 tasks: ['DISPUTED', 'EXPIRED', 'DISPUTED', 'SETTLED', 'SETTLED', 'CANCELLED'] |

## Suppliers

| Supplier | Kind | List price | Floor |
|---|---|---|---|
| s0-honest | honest_cheap | 83 | 78 |
| s1-premium | honest_premium | 117 | 88 |
| s2-greedy | greedy | 174 | 169 |
| s3-injector | injector | 174 | 169 |
| s4-silent | silent | 75 | 69 |
| s5-bad_format | bad_format | 79 | 73 |
| s6-wrong_values | wrong_values | 70 | 64 |
| s7-honest | honest_cheap | 92 | 84 |

## Outcomes by the supplier that won the task

| Supplier kind | Outcomes |
|---|---|
| (no contract) | CANCELLED 6 |
| bad_format | DISPUTED 6 |
| honest_cheap | SETTLED 12 |
| silent | EXPIRED 6 |
| wrong_values | DISPUTED 6 |

## Chaos and attacks

| Tick (s) | Event | Detail |
|---|---|---|
| 15 | chaos | relay replica 0 DOWN |
| 30 | attack | replay attacker re-posted 40 captured envelopes |
| 40 | chaos | buyer0 process CRASHED and restarted from its vault (tasks: ['DISPUTED']) |
| 70 | chaos | relay replica 0 back UP (same storage) |
| 90 | attack | replay attacker re-posted 80 captured envelopes |
| 110 | chaos | ledger RPC DOWN |
| 135 | chaos | ledger RPC back UP |
| 160 | attack | replay attacker re-posted 52 captured envelopes |

## Contracts

| Buyer | Engine | Task | State | Winner | Kind | Price | Escrow | Policy blocks  Excluded by own evidence |
|---|---|---|---|---|---|---|---|---|---|
| buyer0 | reference | 94886eb1f4 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | - | - |
| buyer0 | reference | 015bbe95a9 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s6-wrong_values |
| buyer0 | reference | a5e9d667e2 | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | EXPOSURE_LIMIT | s6-wrong_values |
| buyer0 | reference | dc6a5c5211 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 03be68139f | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 735576a86e | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 9415a1fa7a | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer1 | simulated-llm | d4a1be236a | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s6-wrong_values |
| buyer1 | simulated-llm | 7f43a1d8a2 | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s6-wrong_values |
| buyer1 | simulated-llm | 5a839e97c8 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | d1abd67ba1 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 9f64e3332e | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer2 | reference | 0294b4f855 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | - | - |
| buyer2 | reference | 4d927f8e5f | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s6-wrong_values |
| buyer2 | reference | b3647c68e3 | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | EXPOSURE_LIMIT | s6-wrong_values |
| buyer2 | reference | cdab32b7b6 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | 90983ffc51 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | 97c60bcbfe | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | fa8d739e99 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer3 | simulated-llm | 33fa1a0543 | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s6-wrong_values |
| buyer3 | simulated-llm | 643269986d | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s6-wrong_values |
| buyer3 | simulated-llm | a72d241fe3 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | e8f607ef29 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 5e87674f6f | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer4 | reference | 94d871dfee | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | - | - |
| buyer4 | reference | cdad79d3c5 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s6-wrong_values |
| buyer4 | reference | 0769bbf02c | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | EXPOSURE_LIMIT | s6-wrong_values |
| buyer4 | reference | 6e1a515bbd | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | 35135af781 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | 729ea254c0 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 090e0cab1c | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer5 | simulated-llm | f78d514a24 | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s6-wrong_values |
| buyer5 | simulated-llm | 33cdf957aa | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s6-wrong_values |
| buyer5 | simulated-llm | 0272298bc9 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | a237f1377d | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | aee97508fe | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
