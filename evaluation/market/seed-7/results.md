# Market simulation

Seed 7: 6 Cripto buyers (36 tasks), 8 suppliers, 305 simulated seconds, 37.1 s wall time. Real crypto, policy, relays, runtimes and SIMULATED ledger; only the clock is simulated.

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
| replayed messages are detected and produce no new effects | PASS | 170 replayed envelopes posted; 891 duplicates dropped at ingest, 0 rejected as REPLAY |
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
| 160 | attack | replay attacker re-posted 50 captured envelopes |

## Contracts

| Buyer | Engine | Task | State | Winner | Kind | Price | Escrow | Policy blocks  Excluded by own evidence |
|---|---|---|---|---|---|---|---|---|---|
| buyer0 | reference | 05d13ead58 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | - | - |
| buyer0 | reference | 947fac1040 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s6-wrong_values |
| buyer0 | reference | 1eb13ab325 | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | EXPOSURE_LIMIT | s6-wrong_values |
| buyer0 | reference | 366aea7b19 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 3f6052a518 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 031a984e54 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 74bc8521ad | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer1 | simulated-llm | f09fcc67af | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s6-wrong_values |
| buyer1 | simulated-llm | 623498226a | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s6-wrong_values |
| buyer1 | simulated-llm | 23237f26c4 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 3625bdef7f | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | ff755dff43 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer2 | reference | 8202a76202 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | - | - |
| buyer2 | reference | b7c92ccd76 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s6-wrong_values |
| buyer2 | reference | b12501234e | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | EXPOSURE_LIMIT | s6-wrong_values |
| buyer2 | reference | 26fb2038bf | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | f1094cca49 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | 5a52d44aca | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 77599bc4a6 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer3 | simulated-llm | eef6489e27 | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s6-wrong_values |
| buyer3 | simulated-llm | 62928ffdf2 | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s6-wrong_values |
| buyer3 | simulated-llm | 56eee33fb6 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 6948337dd2 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | a549d4c43f | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer4 | reference | 5ef632bd89 | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | - | - |
| buyer4 | reference | 18323d68c3 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s6-wrong_values |
| buyer4 | reference | 775078cad3 | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | EXPOSURE_LIMIT | s6-wrong_values |
| buyer4 | reference | e7f230735c | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | a5a8b15300 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | eb6f6488ce | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 09d10b2ffa | DISPUTED | s6-wrong_values | wrong_values | 70 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer5 | simulated-llm | 2f4951c642 | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s6-wrong_values |
| buyer5 | simulated-llm | da4b2eae1d | DISPUTED | s5-bad_format | bad_format | 79 | DISPUTED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s6-wrong_values |
| buyer5 | simulated-llm | 6b10748653 | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 2a2fa4069b | SETTLED | s0-honest | honest_cheap | 78 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 2ac1217882 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
