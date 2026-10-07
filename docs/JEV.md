# Jev-style decisions and benchmark

Cripto decides locally in the Jev style described in section 7 of the specification: typed questions
with closed answer spaces, answered by a local model through likelihoods, never through free text.

| Interface | Question | Answer | Used by the agent for |
|---|---|---|---|
| **choice** | pick one option from a closed list | probability per option | the next action: accept quote q, counteroffer q at a grid price, reject |
| **score** | score every option | one number per option | ranking the received quotes |
| **binary** | yes/no about the context | P(yes) | checks such as "within budget?", "does this text give instructions?" |

The model server exposes them as `POST /jev/choice`, `/jev/score` and `/jev/binary`. The prompts and
continuations are fixed and declared in `pactmesh/jev.py` (`FORMAT`, `render_context`, `option_text`).
The runtime engine (`--engine jev`) and the benchmark builder use **the same rendering code**, and a
test asserts it. The benchmark therefore measures exactly what the agent runs.

Whatever the model answers, every executable action still goes through the deterministic policy.

## Benchmark protocol (sections 7 and 14)

| Rule | How it is enforced |
|---|---|
| Split by template, not by random rows | train / validation / test use phrasing templates 0 / 1 / 2; every family in every split; a test asserts no context text is shared |
| Unseen adversarial phrasing | injection phrases in validation/test differ from train; the rule engine's regex was not written for them |
| Gold labels from ground truth | the generator knows price vs budget, delivery, exact asset string (Cyrillic look-alikes included), signature, validity and whether a text is an injection |
| Calibrate on validation only | one temperature per task type, grid search on validation NLL; test untouched |
| Frozen test split | `manifest.json` stores SHA-256 of every split; the runner refuses a modified split (tested) |
| Report counts and intervals | accuracy with Wilson 95% CI, macro F1, per-class errors, Brier, ECE (10 equal-width bins), coverage/accuracy at 0.5/0.7/0.9, accuracy by family |
| Safety, not just accuracy | every ACCEPT on test is sent through the real `PolicyEngine` with a genuinely signed quote; unsafe recommendations, blocks by code and executed violations are reported |
| Reproducible | fixed seed, generator version, git commit in the report, per-item predictions in `evaluation/jev/predictions/` |

Dataset: 894 train, 683 validation, 683 test items (choice, score and binary over 7 families: normal,
boundary, injection, expired, look-alike asset, forged signature, slow).

```bash
python -m pactmesh jev-build                                  # regenerate splits + manifest (deterministic)
python -m pactmesh jev-bench                                  # reference rules + gullible test double
python -m pactmesh jev-bench --model-url http://127.0.0.1:9000  # + a served model (e.g. Laya)
scripts/run_laya.sh                                           # serve Laya, benchmark, calibrated safety eval, demo
```

## Current results (`evaluation/jev/results.md`)

Engines measured so far are the deterministic reference rules and the gullible test double. The
choice-scoring path was also run end to end on a real Hugging Face causal LM: a tiny model with random
weights, built locally, as a pipeline check. It scored near chance and calibration flattened its
confidence, as expected. Its numbers are not published as results. **Laya has not been run yet**
(Hugging Face unreachable from the build environment).

What the current numbers already show:
- the deterministic rule engine is strong on exact checks (asset string, budget, delivery: 100%) but
  its injection detector fails on unseen phrasing (75% on the injection family). Fixed patterns do not
  generalize; this is where a model can add value;
- the gullible double is worse on choice (0.704 vs 0.861) and recommends 27 unsafe accepts on test;
- **every unsafe accept from every engine was blocked by the real policy: 0 executed violations.**
