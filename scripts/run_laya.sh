#!/usr/bin/env bash
# Cripto's Jev-style decision engine on Laya (or any local Hugging Face causal LM):
#   1. serve the model (choice / score / binary endpoints)
#   2. Jev benchmark: temperature fitted on validation, metrics on the frozen test split
#   3. 300-scenario safety evaluation through the real policy, using the calibrated model
#   4. full demo with Cripto deciding through the model
#
#   scripts/run_laya.sh                                   # convaiinnovations/laya
#   MODEL=<hf id or local path> REVISION=<commit> scripts/run_laya.sh
#
# Needs access to huggingface.co the first time (weights are cached afterwards).
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL="${MODEL:-convaiinnovations/laya}"
PORT="${PORT:-9000}"
PY="${PYTHON:-python}"
URL="http://127.0.0.1:$PORT"

"$PY" -c "import torch, transformers" 2>/dev/null || "$PY" -m pip install -r requirements-model.txt
"$PY" -m pactmesh model-server ${BACKEND:+--backend $BACKEND} --hf "$MODEL" ${REVISION:+--revision "$REVISION"} \
    --port "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 900); do
  curl -sf "$URL/health" >/dev/null && break
  kill -0 $SERVER 2>/dev/null || { echo "model server exited (see errors above)"; exit 1; }
  sleep 1
done
curl -s "$URL/health"; echo

[ -f evaluation/jev/manifest.json ] || "$PY" -m pactmesh jev-build
"$PY" -m pactmesh jev-bench --model-url "$URL"

T=$("$PY" -c "import json; r=json.load(open('evaluation/jev/results.json')); \
print(r['engines'][-1]['tasks']['choice']['test']['temperature'])")
echo "calibrated choice temperature (validation split): $T"

"$PY" -m pactmesh eval --engine jev --model-url "$URL" --jev-temperature "$T" --out evaluation/results-model.json
"$PY" -m pactmesh demo --engine jev+fallback --model-url "$URL" --jev-temperature "$T"
echo "Jev benchmark: evaluation/jev/results.md   Safety eval: evaluation/results-model.json"
