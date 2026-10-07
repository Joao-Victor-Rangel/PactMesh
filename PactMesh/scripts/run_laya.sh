#!/usr/bin/env bash
# Cripto's typed decision engine on Laya (or any local Hugging Face causal LM). No paid service:
#   1. serve the model (choice / score / binary endpoints)
#   2. Laya benchmark: temperature fitted on validation, metrics on the frozen test split
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

[ -f evaluation/laya/manifest.json ] || "$PY" -m pactmesh laya-build
"$PY" -m pactmesh laya-bench --model-url "$URL"

T=$("$PY" -c "import json; r=json.load(open('evaluation/laya/results.json')); \
print(r['engines'][-1]['tasks']['choice']['test']['temperature'])")
echo "calibrated choice temperature (validation split): $T"

"$PY" -m pactmesh eval --engine laya --model-url "$URL" --laya-temperature "$T" --out evaluation/results-model.json
"$PY" -m pactmesh demo --engine laya+fallback --model-url "$URL" --laya-temperature "$T"
echo "Laya benchmark: evaluation/laya/results.md   Safety eval: evaluation/results-model.json"
