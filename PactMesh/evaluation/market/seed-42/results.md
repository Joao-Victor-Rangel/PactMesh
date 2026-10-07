# Market simulation

Seed 42: 6 Cripto buyers (36 tasks), 8 suppliers, 349 simulated seconds, 43.9 s wall time. Real crypto, policy, relays, runtimes and SIMULATED ledger; only the clock is simulated.

**ALL INVARIANTS HOLD**

| Invariant | Result | Detail |
|---|---|---|
| money conservation | PASS | CRPT-TEST in wallets + escrows: start 14000, end 14000 |
| no buyer exceeds its budget | PASS | max committed 319 of 400 per buyer |
| no contract above the task budget | PASS | 0 contracts above 100 |
| no double payment | PASS | 12 releases, each escrow released at most once |
| bad deliveries are disputed, never paid | PASS | 12 bad deliveries -> 12 disputed, 0 paid |
| silent suppliers are refunded after the deadline | PASS | 6 silent -> 6 refunded |
| greedy/injector suppliers never paid above budget | PASS | 0 paid |
| every settled receipt verifies | PASS | 12/12 receipts valid |
| every tampered receipt is detected | PASS | 12/12 tampered copies rejected |
| relays never see plaintext or buyer identities | PASS | markers found: none |
| replayed messages are detected and produce no new effects | PASS | 170 replayed envelopes posted; 934 duplicates dropped at ingest, 0 rejected as REPLAY |
| no buyer contracts again with a supplier it has evidence against | PASS | 0 repeat contracts; 30 later tasks excluded suppliers using their own evidence |
| liveness: every task reached a terminal state | PASS | 0 stuck |
| buyer crash + restart: its tasks still finished | PASS | buyer0 tasks: ['DISPUTED', 'DISPUTED', 'EXPIRED', 'SETTLED', 'SETTLED', 'CANCELLED'] |

## Suppliers

| Supplier | Kind | List price | Floor |
|---|---|---|---|
| s0-honest | honest_cheap | 88 | 84 |
| s1-premium | honest_premium | 105 | 92 |
| s2-greedy | greedy | 185 | 180 |
| s3-injector | injector | 154 | 149 |
| s4-silent | silent | 81 | 75 |
| s5-bad_format | bad_format | 71 | 65 |
| s6-wrong_values | wrong_values | 80 | 74 |
| s7-honest | honest_cheap | 89 | 81 |

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
| buyer0 | reference | eccb03e274 | DISPUTED | s5-bad_format | bad_format | 71 | DISPUTED | - | - |
| buyer0 | reference | 56bb789c9e | DISPUTED | s6-wrong_values | wrong_values | 80 | DISPUTED | - | s5-bad_format |
| buyer0 | reference | 429eb52768 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s5-bad_format, s6-wrong_values |
| buyer0 | reference | c9b09a46ed | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 7743482ee0 | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 0bf6d3fc05 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 199c3e2c84 | DISPUTED | s5-bad_format | bad_format | 71 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer1 | simulated-llm | 0facc4f491 | DISPUTED | s6-wrong_values | wrong_values | 80 | DISPUTED | BUDGET_EXCEEDED | s5-bad_format |
| buyer1 | simulated-llm | 589efb8ebf | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 7efdfadadc | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 240a2dc6cf | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | e70247e3c9 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | e5300c48aa | DISPUTED | s5-bad_format | bad_format | 71 | DISPUTED | - | - |
| buyer2 | reference | d7f0c8a30b | DISPUTED | s6-wrong_values | wrong_values | 80 | DISPUTED | - | s5-bad_format |
| buyer2 | reference | a5a134c3e2 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s5-bad_format, s6-wrong_values |
| buyer2 | reference | aafd8a0899 | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | f9ab4d703e | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | 87de36825b | CANCELLED | - | - | - | - | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | bd13ee0653 | DISPUTED | s5-bad_format | bad_format | 71 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer3 | simulated-llm | d4706259e1 | DISPUTED | s6-wrong_values | wrong_values | 80 | DISPUTED | BUDGET_EXCEEDED | s5-bad_format |
| buyer3 | simulated-llm | 84500036d7 | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 6ade195c4e | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | f0470f0514 | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 549c409e6d | CANCELLED | - | - | - | - | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | 149d56752f | DISPUTED | s5-bad_format | bad_format | 71 | DISPUTED | - | - |
| buyer4 | reference | 7f3ca7bfd4 | DISPUTED | s6-wrong_values | wrong_values | 80 | DISPUTED | - | s5-bad_format |
| buyer4 | reference | fd9d55cbe1 | EXPIRED | s4-silent | silent | 75 | REFUNDED | - | s5-bad_format, s6-wrong_values |
| buyer4 | reference | 47521bb582 | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | b06e67f742 | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | f86585e74a | CANCELLED | - | - | - | - | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 35fd289ca9 | DISPUTED | s5-bad_format | bad_format | 71 | DISPUTED | BUDGET_EXCEEDED | - |
| buyer5 | simulated-llm | a82e1b1d25 | DISPUTED | s6-wrong_values | wrong_values | 80 | DISPUTED | BUDGET_EXCEEDED | s5-bad_format |
| buyer5 | simulated-llm | af0576b159 | EXPIRED | s4-silent | silent | 75 | REFUNDED | BUDGET_EXCEEDED | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | d585cf3c1e | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 30dd6a6462 | SETTLED | s0-honest | honest_cheap | 84 | RELEASED | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | ac55c88bef | CANCELLED | - | - | - | - | BUDGET_EXCEEDED, EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
