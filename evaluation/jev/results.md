# Jev-style decision benchmark

Generator `pactmesh-jev-bench/1`, seed 20261007, code `20c15d0`. Test split SHA-256 `e95befff862c25fe…` (verified before running).
Splits: train 894 items (template 0), validation 683 items (template 1), test 683 items (template 2).
Calibration: single temperature per task type, grid search minimizing NLL on the validation split only. ECE: 10 equal-width bins over top-label confidence.

## choice

| Engine | Accuracy (95% CI) | Macro F1 | Brier | ECE | T | Coverage@0.9 (acc) |
|---|---|---|---|---|---|---|
| pactmesh-reference-rules | 0.861 (0.786–0.912) | 0.8866 | 0.2783 | 0.1391 | 1.0 | 1.0 (0.8609) |
| simulated-llm-gullible | 0.704 (0.615–0.780) | 0.7891 | 0.5913 | 0.2957 | 1.0 | 1.0 (0.7043) |

## score

| Engine | Accuracy (95% CI) | Macro F1 | Brier | ECE | T | Pairwise order | Coverage@0.9 (acc) |
|---|---|---|---|---|---|---|---|
| pactmesh-reference-rules | 0.922 (0.830–0.966) | - | 0.1884 | 0.1092 | 1.0 | 0.9569 | 0.6094 (1.0) |
| simulated-llm-gullible | 0.797 (0.683–0.877) | - | 0.4307 | 0.2263 | 1.0 | 0.8448 | 0.625 (0.8) |

## binary

| Engine | Accuracy (95% CI) | Macro F1 | Brier | ECE | T | Coverage@0.9 (acc) |
|---|---|---|---|---|---|---|
| pactmesh-reference-rules | 0.964 (0.944–0.977) | 0.9638 | 0.0714 | 0.0357 | 1.0 | 1.0 (0.9643) |
| simulated-llm-gullible | 0.929 (0.903–0.948) | 0.9271 | 0.1428 | 0.0714 | 1.0 | 1.0 (0.9286) |

## Safety: every ACCEPT on the test split sent through the real PolicyEngine

| Engine | Unsafe accepts recommended | Blocked by policy | Executed violations |
|---|---|---|---|
| pactmesh-reference-rules | 9 | 9 (ASSET_NOT_ALLOWED 5, QUOTE_SIGNATURE_INVALID 4) | 0 |
| simulated-llm-gullible | 27 | 27 (BUDGET_EXCEEDED 18, ASSET_NOT_ALLOWED 5, QUOTE_SIGNATURE_INVALID 4) | 0 |

## Binary accuracy by family (test)

| Engine | boundary | expired | forged_signature | injection | normal | slow | wrong_asset |
|---|---|---|---|---|---|---|---|
| pactmesh-reference-rules | 1.00 | 1.00 | 1.00 | 0.75 | 1.00 | 1.00 | 1.00 |
| simulated-llm-gullible | 1.00 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 |

Gold labels come from the generator's ground truth. The reference engine is deterministic code over the facts a rule engine can read; it does not check signatures or validity (the policy does). Counts are small: read the confidence intervals, not just the point estimates.
