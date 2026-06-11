#!/usr/bin/env bash
# Upload the GGUF artifacts in build-gguf/ to Hugging Face — hang-proof and resumable.
#
#   ./scripts/upload-gguf.sh
#
# Multi-GB uploads can stall mid-Xet with no error. This script restarts the uploader
# whenever output stops for 150s; uploaded chunks PERSIST server-side, so every restart
# resumes where it left off (already-complete files are skipped by size check).
# Safe to Ctrl-C and re-run at any time.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/mlx-env/bin/python"
SRC="$ROOT/build-gguf"
REPO="${HF_GGUF_REPO:-navindbhudiya/qwen2.5-coder-7b-magento-v4-gguf}"
LOG="${TMPDIR:-/tmp}/magepilot-gguf-upload.log"

[ -d "$SRC" ] || { echo "✗ $SRC missing — run ./scripts/convert-gguf.sh first"; exit 1; }
"$PY" -c "from huggingface_hub import whoami; print('HF user:', whoami()['name'])" \
  || { echo "✗ not logged in — run: $PY -m huggingface_hub.commands.huggingface_cli login"; exit 1; }

UPLOADER="$(mktemp)"
cat > "$UPLOADER" <<PYEOF
import os
from huggingface_hub import HfApi
api = HfApi()
api.create_repo("$REPO", repo_type="model", exist_ok=True)
remote = {i.path: i.size for i in api.list_repo_tree("$REPO", recursive=True)}
for name in sorted(os.listdir("$SRC")):
    local = os.path.join("$SRC", name)
    if not os.path.isfile(local):
        continue
    if remote.get(name) == os.path.getsize(local):
        print(f"[skip] {name} already complete on the hub", flush=True)
        continue
    print(f"[upload] {name}", flush=True)
    api.upload_file(path_or_fileobj=local, path_in_repo=name, repo_id="$REPO")
    print(f"[done] {name}", flush=True)
print("[ALL DONE]", flush=True)
PYEOF

echo "uploading $SRC → https://huggingface.co/$REPO   (log: $LOG)"
: > "$LOG"
trap 'kill "$PID" 2>/dev/null; rm -f "$UPLOADER"; exit 130' INT TERM

for round in $(seq 1 500); do
  echo "=== round $round $(date '+%H:%M:%S') ==="
  "$PY" "$UPLOADER" 2>&1 | tee -a "$LOG" &
  PID=$!
  LAST=0; STALL=0
  while kill -0 "$PID" 2>/dev/null; do
    sleep 30
    SIZE="$(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null || echo 0)"
    if [ "$SIZE" = "$LAST" ]; then STALL=$((STALL + 30)); else STALL=0; LAST="$SIZE"; fi
    if [ "$STALL" -ge 150 ]; then
      echo "[watchdog] stalled ${STALL}s — restarting (progress is preserved)"
      kill -9 "$PID" 2>/dev/null
      pkill -9 -f "$UPLOADER" 2>/dev/null
      break
    fi
  done
  wait "$PID" 2>/dev/null
  if grep -q "ALL DONE" "$LOG"; then
    rm -f "$UPLOADER"
    echo "✓ all files on https://huggingface.co/$REPO"
    echo "  try it:  ollama run hf.co/$REPO:Q4_K_M"
    exit 0
  fi
  sleep 10
done
rm -f "$UPLOADER"
echo "✗ gave up after 500 rounds — check $LOG"
exit 1
