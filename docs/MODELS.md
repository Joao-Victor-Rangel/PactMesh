# Plugging in a local model (Laya or any open-weights model)

The decision engine only recommends. Whatever model you plug in, its output must be one closed action
over the offered quotes. A counteroffer price must come from the policy's grid. Anything else becomes
`ABSTAIN` (`invalid_output`), or the declared reference fallback with `+fallback`. The policy engine
then re-checks every recommendation, so a fooled model cannot move money.

## Engines

| `--engine` | What it is |
|---|---|
| `reference` | deterministic rules with transparent weights (baseline) |
| `simulated-llm` | **test double** that follows instructions hidden in supplier text; used to demo the policy |
| `openai-compat[+fallback]` | any local server exposing `/v1/chat/completions` (Ollama, llama.cpp `server`, vLLM, LM Studio) |
| `http[+fallback]` | a custom server: `POST {state, options, allowed_actions}` -> `{action, quote_id, counter_price, scores, rationale}` |

Supplier descriptions reach the model wrapped as `{"untrusted_supplier_text": ...}`. The system
prompt states that this text is data, never an instruction. The prompt is not the protection; the
policy is.

## Example with Ollama

```bash
ollama pull llama3.2            # or any model you want to evaluate
python -m pactmesh eval --engine openai-compat --model-url http://127.0.0.1:11434/v1 \
    --model-name llama3.2 --model-revision <digest from `ollama show`>
python -m pactmesh buyer --engine openai-compat+fallback --model-url http://127.0.0.1:11434/v1 --model-name llama3.2
```

`eval` runs the same 300 seeded scenarios for the reference rules, the gullible test double and your
model, and writes `evaluation/results.json` with:
- executed policy violations (must stay 0)
- unsafe recommendations blocked, by code
- times the model followed an injected instruction
- output validity (`valid` / `invalid_output` / `unavailable`) and coverage (non-abstain rate)
- macro F1 against rule-derived labels, and latency p50/p95

## Laya (the specification's candidate) — one command

Cripto's decisions are typed choice / score / binary questions answered by Laya (no paid service; see
[LAYA.md](LAYA.md) for the interfaces and the benchmark protocol). Use `--engine laya` (or
`laya+fallback`) at runtime.

```bash
scripts/run_laya.sh            # downloads convaiinnovations/laya on first run, serves it, evaluates, demos
# or step by step:
pip install -r requirements-model.txt
python -m pactmesh model-server --hf convaiinnovations/laya --revision <commit> --port 9000
python -m pactmesh eval --engine http --model-url http://127.0.0.1:9000/decide --out evaluation/results-laya.json
python -m pactmesh demo --engine http+fallback --model-url http://127.0.0.1:9000/decide
```

`model-server` uses **choice scoring**: it lists every action the policy grid allows (accept quote q,
counteroffer q at each grid price, reject), asks the model for the log-likelihood of each, and returns the
best one with normalized scores and a confidence value (`--abstain-below` turns low confidence into
ABSTAIN). The output is always a valid typed decision, with no free-text parsing involved.

### What is verified and what is not

| | Status |
|---|---|
| Choice-scoring server, engine contract, abstention, eval integration | tested (`tests/test_modelserver.py`) |
| Real `TransformersBackend` on a genuine Hugging Face causal LM (tiny Llama built locally, random weights) | tested (`tests/test_transformers_backend.py`, needs torch) |
| `scripts/run_laya.sh` end to end (serve, 300-scenario eval, full demo) with that local model | run in the build environment: 0 executed violations; the policy blocked all 40 unsafe recommendations of the random model |
| **Laya's actual weights** | **not yet run**: the build environment cannot reach huggingface.co. Allow that host (and its CDN) in the environment's network settings, or run the script on your machine |

Before you quote Laya numbers: check its model card (license, languages, input size, intended
interface) and pin the revision. If the card prescribes its own choice/scoring API, adapt
`TransformersBackend.score`. Only pass `--trust-remote-code` after reviewing the model's code. If Laya
does not beat the reference rules on `evaluation/results-laya.json`, the specification says to present the
AI as an experiment, and the reference rules remain the default.
