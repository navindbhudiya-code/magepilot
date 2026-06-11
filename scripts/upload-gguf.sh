#!/usr/bin/env bash
# Upload the GGUF artifacts in build-gguf/ to Hugging Face — hang-proof and resumable.
#
#   ./scripts/upload-gguf.sh            # Xet first (resumable), auto-falls back to plain HTTPS
#   ./scripts/upload-gguf.sh --no-xet   # plain HTTPS from the start (most reliable, not resumable)
#
# Why this exists: multi-GB Xet uploads can hang forever with no error — but their
# progress output is also BURSTY (long silent stretches mid-chunk), so "no output for a
# couple of minutes" is NOT a hang. The watchdog therefore tracks the parsed byte
# POSITION and only restarts after 10 minutes without movement; after 3 restarts on the
# same file it switches to plain-HTTPS LFS (the same fix serve.sh uses for downloads).
# Already-complete files are skipped by size, so Ctrl-C + re-run is always safe.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/mlx-env/bin/python"
SRC="$ROOT/build-gguf"
REPO="${HF_GGUF_REPO:-navindbhudiya/qwen2.5-coder-7b-magento-v4-gguf}"
LOG="${TMPDIR:-/tmp}/magepilot-gguf-upload.log"
STALL_LIMIT=600          # seconds without byte-position movement → restart
XET_KILL_LIMIT=3         # restarts on the same file before disabling Xet

[ "${1:-}" = "--no-xet" ] && export HF_HUB_DISABLE_XET=1
[ -d "$SRC" ] || { echo "✗ $SRC missing — run ./scripts/convert-gguf.sh first"; exit 1; }
"$PY" -c "from huggingface_hub import whoami; print('HF user:', whoami()['name'])" \
  || { echo "✗ not logged in to Hugging Face"; exit 1; }

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

# the watchdog's pulse: last byte position + completed-file count from the log
pulse() {
  tr '\r' '\n' < "$LOG" 2>/dev/null | grep -oE '[0-9][0-9.]*[KMG]?B */ *[0-9][0-9.]*[KMG]B' | tail -1
  grep -c '^\[done\]\|^\[skip\]' "$LOG" 2>/dev/null
}
current_file() { grep '^\[upload\]' "$LOG" 2>/dev/null | tail -1; }

echo "uploading $SRC → https://huggingface.co/$REPO"
echo "  xet: $([ -n "${HF_HUB_DISABLE_XET:-}" ] && echo DISABLED || echo on)  ·  log: $LOG"
: > "$LOG"
trap 'kill "$PID" 2>/dev/null; pkill -9 -f "$UPLOADER" 2>/dev/null; rm -f "$UPLOADER"; exit 130' INT TERM

KILLED_FILE=""; KILLS=0
for round in $(seq 1 200); do
  echo "=== round $round $(date '+%H:%M:%S') ==="
  PYTHONUNBUFFERED=1 "$PY" "$UPLOADER" >> "$LOG" 2>&1 &
  PID=$!
  tail -f "$LOG" &
  DISPLAY_PID=$!
  LAST=""; STALL=0
  while kill -0 "$PID" 2>/dev/null; do
    sleep 30
    NOW="$(pulse)"
    if [ "$NOW" = "$LAST" ]; then STALL=$((STALL + 30)); else STALL=0; LAST="$NOW"; fi
    if [ "$STALL" -ge "$STALL_LIMIT" ]; then
      FILE="$(current_file)"
      echo "[watchdog] no byte movement for ${STALL}s on '$FILE' — restarting (progress persists)"
      if [ "$FILE" = "$KILLED_FILE" ]; then KILLS=$((KILLS + 1)); else KILLED_FILE="$FILE"; KILLS=1; fi
      kill -9 "$PID" 2>/dev/null
      pkill -9 -f "$UPLOADER" 2>/dev/null
      if [ "$KILLS" -ge "$XET_KILL_LIMIT" ] && [ -z "${HF_HUB_DISABLE_XET:-}" ]; then
        export HF_HUB_DISABLE_XET=1
        echo "[watchdog] $KILLS stalls on the same file — switching to plain HTTPS (no Xet)"
      fi
      break
    fi
  done
  wait "$PID" 2>/dev/null
  kill "$DISPLAY_PID" 2>/dev/null
  if grep -q "ALL DONE" "$LOG"; then
    rm -f "$UPLOADER"
    echo "✓ all files on https://huggingface.co/$REPO"
    echo "  try it:  ollama run hf.co/$REPO:Q4_K_M"
    exit 0
  fi
  sleep 10
done
rm -f "$UPLOADER"
echo "✗ gave up after 200 rounds — check $LOG"
exit 1
