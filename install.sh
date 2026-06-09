#!/usr/bin/env bash
#
# Magepilot installer — local AI coding assistant for Magento 2 & Hyvä.
#
#   curl -fsSL https://navindbhudiya.com/install.sh | bash
#
# What it does: installs uv (if missing), clones Magepilot to ~/.magepilot, sets up the
# Python environment + dependencies + the Magento knowledge base, and puts the `magepilot`
# command on your PATH. The ~4 GB model is NOT downloaded here — that happens on first
# `magepilot serve`.
#
# Overridable with env vars:
#   MAGEPILOT_HOME    install location           (default: ~/.magepilot)
#   MAGEPILOT_REPO    git URL to clone           (default: the public GitHub repo)
#   MAGEPILOT_BRANCH  branch to track            (default: main)
#
set -euo pipefail

REPO="${MAGEPILOT_REPO:-https://github.com/navindbhudiya-code/magepilot.git}"
BRANCH="${MAGEPILOT_BRANCH:-main}"
HOME_DIR="${MAGEPILOT_HOME:-$HOME/.magepilot}"

# ── pretty output (color only on a real terminal) ───────────────────────────
if [ -t 1 ]; then
  B=$'\033[94m'; G=$'\033[92m'; Y=$'\033[93m'; R=$'\033[91m'; D=$'\033[2m'; X=$'\033[0m'
else
  B=''; G=''; Y=''; R=''; D=''; X=''
fi
info() { echo "${B}magepilot${X} $*"; }
ok()   { echo "${G}✓${X} $*"; }
warn() { echo "${Y}!${X} $*"; }
die()  { echo "${R}✗${X} $*" >&2; exit 1; }

trap 'echo; echo "${R}✗ install failed${X} — see the output above. Re-run it, or open an issue:"; echo "   https://github.com/navindbhudiya-code/magepilot/issues"' ERR

echo
echo "${B}  Magepilot${X} — local AI coding assistant for Magento 2 & Hyvä"
echo "${D}  installs to ${HOME_DIR}${X}"
echo

# ── 1. platform ─────────────────────────────────────────────────────────────
OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS" in
  Darwin)
    if [ "$ARCH" = "arm64" ]; then
      ok "Apple Silicon macOS — the bundled MLX model server is supported"
    else
      warn "Intel macOS — the bundled MLX model server won't run (MLX is Apple-Silicon only)."
      warn "The agent + RAG still work against your own OpenAI-compatible server (see README)."
    fi ;;
  Linux)
    warn "Linux — the bundled MLX model server is Apple-Silicon only. The agent + RAG run here"
    warn "against a model you serve yourself (Ollama/llama.cpp/vLLM); see the README's Linux section." ;;
  *) die "unsupported OS: $OS — macOS (Apple Silicon) or Linux required" ;;
esac

# ── 2. git ──────────────────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
  [ "$OS" = "Darwin" ] && die "git not found — run 'xcode-select --install', then re-run this installer."
  die "git not found — install git with your package manager, then re-run."
fi

# ── 3. uv (Python package manager) ──────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  info "installing uv (Python package manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv still not on PATH after install — see https://docs.astral.sh/uv/getting-started/installation/"
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

# ── 4. disk space (soft warning; the model needs ~5 GB) ─────────────────────
free_gb="$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}')"
if [ -n "${free_gb:-}" ] && [ "$free_gb" -lt 6 ]; then
  warn "only ${free_gb} GB free on your home volume — the model needs ~5 GB. Free some space before 'magepilot serve'."
fi

# ── 5. clone (or update an existing checkout) ───────────────────────────────
if [ -d "$HOME_DIR/.git" ]; then
  info "updating existing install at ${HOME_DIR}…"
  git -C "$HOME_DIR" fetch --depth 1 origin "$BRANCH" -q
  git -C "$HOME_DIR" checkout -q "$BRANCH"
  if ! git -C "$HOME_DIR" merge --ff-only "origin/$BRANCH" -q 2>/dev/null; then
    # Histories diverged (e.g. upstream was rewritten). This checkout is installer-managed,
    # so if the user hasn't edited it, snap to upstream; if they have, leave it alone.
    if [ -z "$(git -C "$HOME_DIR" status --porcelain)" ]; then
      git -C "$HOME_DIR" reset --hard "origin/$BRANCH" -q
      ok "history diverged from upstream — reset to origin/$BRANCH"
    else
      warn "couldn't fast-forward (local changes?) — leaving your copy as-is"
    fi
  fi
elif [ -e "$HOME_DIR" ]; then
  die "${HOME_DIR} exists but isn't a git checkout — move it aside or set MAGEPILOT_HOME, then re-run."
else
  info "cloning magepilot into ${HOME_DIR}…"
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$HOME_DIR" -q
fi
ok "code ready at ${HOME_DIR}"

# ── 6. environment + deps + knowledge base + PATH symlink ───────────────────
info "setting up the environment (deps + Magento knowledge base — a few minutes)…"
"$HOME_DIR/magepilot" install

# ── 7. done ─────────────────────────────────────────────────────────────────
trap - ERR
echo
ok "${G}Magepilot is installed.${X}"
echo
echo "  Next steps:"
echo "    1. ${B}open a new terminal${X}   so the 'magepilot' command is on your PATH"
echo "    2. ${B}magepilot serve${X}       first run downloads the model (~4 GB) → logs/model.log"
echo "    3. ${B}cd${X} into any Magento project and run ${B}magepilot${X}"
echo
echo "  ${D}docs & help:  https://github.com/navindbhudiya-code/magepilot${X}"
echo
