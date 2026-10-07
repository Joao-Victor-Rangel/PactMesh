#!/usr/bin/env bash
# Run Cripto's decision engine on Laya (or any local Hugging Face causal LM):
# serve it, evaluate it on the 300 scenarios, then run the full demo with it.
#
#   scripts/run_laya.sh                              # convaiinnovations/laya
#   MODEL=<hf id or local path> REVISION=<commit> scripts/run_laya.sh
#
# Needs network access to huggingface.co the first time (weights are cached afterwards).
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${MODEL:-convaiinnovations/laya}"
PORT="${PORT:-9000}"
PY="${PYTHON:-python}"

"$PY" -c "import torch, transformers" 2>/dev/null || "$PY" -m pip install torch transformers
"$PY" -m pactmesh model-server ${BACKEND:+--backend $BACKEND} --hf "$MODEL" ${REVISION:+--revision "$REVISION"} --port "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 600); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  kill -0 $SERVER 2>/dev/null || { echo "model server exited (see errors above)"; exit 1; }
  sleep 1
done
curl -s "http://127.0.0.1:$PORT/health"; echo
"$PY" -m pactmesh eval --engine http --model-url "http://127.0.0.1:$PORT/decide" \
    --out evaluation/results-model.json
"$PY" -m pactmesh demo --engine http+fallback --model-url "http://127.0.0.1:$PORT/decide"
echo "Evaluation: evaluation/results-model.json"
