# Market simulation

Seed 11: 6 Cripto buyers (36 tasks), 8 suppliers, 305 simulated seconds, 36.2 s wall time. Real crypto, policy, relays, runtimes and SIMULATED ledger; only the clock is simulated.

**ALL INVARIANTS HOLD**

| Invariant | Result | Detail |
|---|---|---|
| money conservation | PASS | CRPT-TEST in wallets + escrows: start 14000, end 14000 |
| no buyer exceeds its budget | PASS | max committed 300 of 400 per buyer |
| no contract above the task budget | PASS | 0 contracts above 100 |
| no double payment | PASS | 12 releases, each escrow released at most once |
| bad deliveries are disputed, never paid | PASS | 12 bad deliveries -> 12 disputed, 0 paid |
| silent suppliers are refunded after the deadline | PASS | 6 silent -> 6 refunded |
| greedy/injector suppliers never paid above budget | PASS | 0 paid |
| every settled receipt verifies | PASS | 12/12 receipts valid |
| every tampered receipt is detected | PASS | 12/12 tampered copies rejected |
| relays never see plaintext or buyer identities | PASS | markers found: none |
| replayed messages are detected and produce no new effects | PASS | 200 replayed envelopes posted; 899 duplicates dropped at ingest, 0 rejected as REPLAY |
| no buyer contracts again with a supplier it has evidence against | PASS | 0 repeat contracts; 30 later tasks excluded suppliers using their own evidence |
| liveness: every task reached a terminal state | PASS | 0 stuck |
| buyer crash + restart: its tasks still finished | PASS | buyer0 tasks: ['DISPUTED', 'EXPIRED', 'DISPUTED', 'SETTLED', 'SETTLED', 'CANCELLED'] |

## Suppliers

| Supplier | Kind | List price | Floor |
|---|---|---|---|
| s0-honest | honest_cheap | 85 | 75 |
| s1-premium | honest_premium | 122 | 95 |
| s2-greedy | greedy | 198 | 193 |
| s3-injector | injector | 172 | 167 |
| s4-silent | silent | 73 | 67 |
| s5-bad_format | bad_format | 72 | 66 |
| s6-wrong_values | wrong_values | 82 | 76 |
| s7-honest | honest_cheap | 86 | 79 |

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
| 160 | attack | replay attacker re-posted 80 captured envelopes |

## Contracts

| Buyer | Engine | Task | State | Winner | Kind | Price | Escrow | Policy blocks  Excluded by own evidence |
|---|---|---|---|---|---|---|---|---|---|
| buyer0 | reference | 28927de07c | DISPUTED | s5-bad_format | bad_format | 72 | DISPUTED | - | - |
| buyer0 | reference | 397b55f13b | EXPIRED | s4-silent | silent | 73 | REFUNDED | - | s5-bad_format |
| buyer0 | reference | 34e5a23ca6 | DISPUTED | s6-wrong_values | wrong_values | 76 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer0 | reference | e1e48ec9ef | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | af975d9ce1 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer0 | reference | 64cd16bf55 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | 13641ddefc | DISPUTED | s5-bad_format | bad_format | 72 | DISPUTED | - | - |
| buyer1 | simulated-llm | 964c6b416b | EXPIRED | s4-silent | silent | 73 | REFUNDED | - | s5-bad_format |
| buyer1 | simulated-llm | 419add89f1 | DISPUTED | s6-wrong_values | wrong_values | 76 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer1 | simulated-llm | 9b13cfef96 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | e0d0bb5572 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer1 | simulated-llm | d5c3374dcd | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer2 | reference | 1a2e9455d8 | DISPUTED | s5-bad_format | bad_format | 72 | DISPUTED | - | - |
| buyer2 | reference | 5ba8910585 | EXPIRED | s4-silent | silent | 73 | REFUNDED | - | s5-bad_format |
| buyer2 | reference | 85eeff2d9f | DISPUTED | s6-wrong_values | wrong_values | 76 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer2 | reference | 3ba6b7056c | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | af930508fa | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer2 | reference | def5a4a0c8 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 17058a7c05 | DISPUTED | s5-bad_format | bad_format | 72 | DISPUTED | - | - |
| buyer3 | simulated-llm | d05addee82 | EXPIRED | s4-silent | silent | 73 | REFUNDED | - | s5-bad_format |
| buyer3 | simulated-llm | 90de165c77 | DISPUTED | s6-wrong_values | wrong_values | 76 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer3 | simulated-llm | 35eff9d283 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | d360a74f92 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer3 | simulated-llm | 0697f4f570 | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer4 | reference | 7f23398c45 | DISPUTED | s5-bad_format | bad_format | 72 | DISPUTED | - | - |
| buyer4 | reference | 3efe7cb989 | EXPIRED | s4-silent | silent | 73 | REFUNDED | - | s5-bad_format |
| buyer4 | reference | 712a03a1eb | DISPUTED | s6-wrong_values | wrong_values | 76 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer4 | reference | c8e9e5e62c | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | e302176ee4 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer4 | reference | 768b563ccd | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 62540c40eb | DISPUTED | s5-bad_format | bad_format | 72 | DISPUTED | - | - |
| buyer5 | simulated-llm | 78596f3100 | EXPIRED | s4-silent | silent | 73 | REFUNDED | - | s5-bad_format |
| buyer5 | simulated-llm | 24daf7de0a | DISPUTED | s6-wrong_values | wrong_values | 76 | DISPUTED | EXPOSURE_LIMIT | s5-bad_format |
| buyer5 | simulated-llm | 36b52f483e | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 3a0f4cad70 | SETTLED | s0-honest | honest_cheap | 76 | RELEASED | EXPOSURE_LIMIT | s5-bad_format, s6-wrong_values |
| buyer5 | simulated-llm | 497d42fe4f | CANCELLED | - | - | - | - | BUDGET_EXCEEDED | s4-silent, s5-bad_format, s6-wrong_values |
