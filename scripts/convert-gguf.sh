#!/usr/bin/env bash
# Convert the Magepilot fine-tune (MLX LoRA adapters) to GGUF for Ollama/llama.cpp.
#
#   ./scripts/convert-gguf.sh [adapter-dir] [out-dir]
#
# Pipeline (the MLX 4-bit fused model canNOT be converted directly — its quantization
# format is MLX-native):
#   1. mlx_lm.fuse --dequantize : adapter + 4-bit base → fp16 HF safetensors (~15 GB)
#   2. convert_hf_to_gguf.py --outtype q8_0 : → near-lossless Q8_0 GGUF (~8 GB)
#      (q8_0 directly, NOT f16 first — halves the peak disk requirement)
#   3. llama-quantize --allow-requantize : Q8_0 → Q4_K_M (~4.4 GB, the Ollama default)
#
# Needs ~25 GB free disk, `brew install llama.cpp`, and in the venv:
#   uv pip install torch gguf 'transformers>=4.45' sentencepiece
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADAPTERS="${1:-$ROOT/training/adapters-v4}"
OUT="${2:-$ROOT/build-gguf}"
BASE="mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
NAME="qwen2.5-coder-7b-magento-v4"
PY="$ROOT/mlx-env/bin/python"
LLAMA_CPP_SRC="${LLAMA_CPP_SRC:-/tmp/llama.cpp-convert}"

command -v llama-quantize >/dev/null || { echo "✗ llama-quantize missing — brew install llama.cpp"; exit 1; }
[ -f "$ADAPTERS/adapters.safetensors" ] || { echo "✗ no adapters.safetensors in $ADAPTERS"; exit 1; }
[ -f "$LLAMA_CPP_SRC/convert_hf_to_gguf.py" ] || {
  echo "fetching the llama.cpp convert script…"
  git clone -q --depth 1 https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_SRC"
}

free_gb="$(df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}')"
[ "$free_gb" -ge 25 ] || { echo "✗ need ~25 GB free, have ${free_gb} GB"; exit 1; }

mkdir -p "$OUT"
echo "→ 1/3 fusing adapter onto $BASE (dequantized fp16)…"
"$PY" -m mlx_lm.fuse --model "$BASE" --adapter-path "$ADAPTERS" \
  --save-path "$OUT/fused-fp16" --dequantize

echo "→ 2/3 converting to GGUF Q8_0…"
"$PY" "$LLAMA_CPP_SRC/convert_hf_to_gguf.py" "$OUT/fused-fp16" \
  --outtype q8_0 --outfile "$OUT/$NAME-q8_0.gguf"
rm -rf "$OUT/fused-fp16"

echo "→ 3/3 quantizing Q4_K_M…"
llama-quantize --allow-requantize "$OUT/$NAME-q8_0.gguf" "$OUT/$NAME-q4_k_m.gguf" Q4_K_M

ls -lh "$OUT"/*.gguf
echo "✓ done. Smoke-test:  llama-completion -m $OUT/$NAME-q4_k_m.gguf --temp 0.3 -n 200 -p '...'"
echo "  Ollama:           cd $OUT && ollama create magepilot -f $ROOT/scripts/Modelfile"
