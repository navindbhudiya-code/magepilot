#!/usr/bin/env bash
# Launch the fine-tuned Magento model as a local OpenAI-compatible server (:8080).
# Sampling defaults baked in — greedy decoding (temp 0) causes repetition loops.
set -euo pipefail
cd "$(dirname "$0")/.."
source mlx-env/bin/activate
mkdir -p logs
mlx_lm.server \
  --model ./models/qwen2.5-coder-7b-magento-v4 \
  --host 127.0.0.1 --port 8080 \
  --temp 0.3 --top-p 0.95 2>&1 | tee logs/server.log
