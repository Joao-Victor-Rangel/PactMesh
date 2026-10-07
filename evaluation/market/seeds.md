# Market simulation across seeds

6 buyers, 8 suppliers, same chaos schedule; each seed changes supplier prices and the envelopes the replay attacker picks.

| Seed | Tasks | Settled | Disputed | Expired (refunded) | Cancelled | Invariants | Wall time |
|---|---|---|---|---|---|---|---|
| 7 | 36 | 12 | 12 | 6 | 6 | 14/14 | 37.1 s |
| 11 | 36 | 12 | 12 | 6 | 6 | 14/14 | 36.2 s |
| 23 | 36 | 18 | 12 | 6 | 0 | 14/14 | 33.9 s |
| 42 | 36 | 12 | 12 | 6 | 6 | 14/14 | 43.9 s |
