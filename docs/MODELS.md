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

## Laya

Laya is the candidate named in the specification. Serve the checkpoint behind any OpenAI-compatible
server that supports its architecture, then run the commands above with its model name and pinned
revision. This was **not** verified from the build environment (Hugging Face was unreachable), so check
the model card for license, language and input limits first. Report results exactly as measured; if
Laya does not beat the reference rules, the specification says to present the AI as an experiment.
