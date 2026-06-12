"""Default configuration constants for MagePilot (migrated from agent/config.py).

These remain plain module-level names — tests and callers may rebind them on the
`magepilot.config` package (which star-imports this module) and every consumer reads
them at call time via `config.X`.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# src/magepilot/config/ → src/magepilot/ → src/ → repo root. MAGEPILOT_HOME overrides
# (e.g. when the package is installed outside the checkout).
REPO_ROOT = os.environ.get("MAGEPILOT_HOME") or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

# --- Codebase under analysis (set via `--root` or AGENT_CODEBASE env) ---
DEFAULT_CODEBASE = os.environ.get("AGENT_CODEBASE", "")

# --- User cache: per-project indexes + the undo journal live here (NOT in the repo, so they're
#     shared across clones and survive `git clean`). Override with MAGEPILOT_CACHE / XDG_CACHE_HOME.
CACHE_DIR = os.environ.get("MAGEPILOT_CACHE") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "magepilot")

# --- Code vector store (per-project subdirs under CODE_CHROMA_PATH/<hash-of-path>/) ---
CODE_CHROMA_PATH = os.path.join(CACHE_DIR, "index")
CODE_COLLECTION = "magento_code"
ROOT_MARKER = os.path.join(CODE_CHROMA_PATH, "last_root.txt")   # most-recent index (default-root fallback)

# --- Undo journal: reverse ops for the last `make` (so /undo can revert it exactly) ---
UNDO_FILE = os.path.join(CACHE_DIR, "last_make.json")

# --- Agent runs: checkpoints + event transcripts for `do` / `resume` live here ---
RUNS_DIR = os.path.join(CACHE_DIR, "runs")

# --- Reuse the existing Magento knowledge base (facts) for the kb_search tool ---
KB_CHROMA_PATH = os.path.join(REPO_ROOT, "rag", ".chroma")
KB_COLLECTION = "magento_kb"

# --- Local model server (OpenAI-compatible mlx_lm.server) ---
MODEL_SERVER = os.environ.get("MODEL_SERVER", "http://127.0.0.1:8080/v1")
MODEL_MATCH = "magento"  # the style fine-tune (used by the chat assistant / RAG, not the agent loop)
# The agent's REASONING model. The base Instruct model follows the tool/ReAct format far more
# reliably than the style-tuned fine-tune (which is overfit to writing prose/code answers); the
# fine-tune + RAG remain the code-generation/style layer. "" = auto-pick the base from /v1/models.
AGENT_MODEL = os.environ.get("AGENT_MODEL", "")
SAMPLING = {"temperature": 0.15, "top_p": 0.9, "repetition_penalty": 1.1, "max_tokens": 900}

# --- Indexing ---
INDEX_EXTENSIONS = (".php", ".phtml", ".xml", ".js", ".graphqls", ".less", ".css", ".html", ".csv")
SKIP_DIRS = {
    "vendor", "node_modules", "generated", "var", "pub", ".git", ".idea",
    "dev", "setup", ".github", "tests", "Test", "test", "__pycache__",
}
# On-demand search (grep / find_files / read_file resolution) MAY descend into vendor/ — that is
# where core & third-party Magento classes live, so symbol lookups ("which class implements X")
# need it. The INDEXER keeps using SKIP_DIRS (embedding all of vendor would be tens of thousands
# of files); only interactive search reaches vendor, and it still skips the noisy generated dirs.
SEARCH_SKIP_DIRS = SKIP_DIRS - {"vendor"}
MAX_FILE_BYTES = 512 * 1024     # skip files larger than this
CHUNK_LINES = 48                # lines per code chunk
CHUNK_OVERLAP = 8               # overlap between consecutive chunks

# --- Agent loop ---
MAX_STEPS = 8                   # hard cap on think->act->observe iterations
MAX_OBS_CHARS = 1600            # truncate each tool observation to this
TOP_K_CODE = 5
TOP_K_KB = 4

# --- Database (read-only debugging) ---
# Credentials are read from <root>/app/etc/env.php. For Docker/Warden where the DB host in
# env.php ("db") isn't reachable from the host, override with these (e.g. 127.0.0.1 + mapped port).
DB_HOST = os.environ.get("AGENT_DB_HOST", "")
DB_PORT = os.environ.get("AGENT_DB_PORT", "")
DB_ROW_LIMIT = 200              # auto-applied LIMIT for SELECTs that don't have one
# Transport: 'auto' tries host mysql, then a docker db container, then `warden db connect`.
DB_MODE = os.environ.get("AGENT_DB_MODE", "auto")          # auto | host | docker | warden
DB_CONTAINER = os.environ.get("AGENT_DB_CONTAINER", "")    # docker mode: the db container name

# --- Deterministic make rails (docs/architecture/03/07) ---
MANIFEST_RETRIES = 2            # coder re-prompts per missing manifest file
REPAIR_ROUNDS = 2               # validate→repair loop rounds after plan assembly
PHP_LINT_TIMEOUT = 20           # seconds for `php -l` (validation skips when php is absent)

# --- Auto-update (updater/): silent background check + staged apply, Claude-Code style ---
UPDATE_REPO_SLUG = "navindbhudiya-code/magepilot"
UPDATE_API_URL = f"https://api.github.com/repos/{UPDATE_REPO_SLUG}/releases/latest"
UPDATE_CHECK_INTERVAL_S = 24 * 3600     # background check at most once a day
UPDATE_LOCK_STALE_S = 600               # a lock older than this is from a dead updater
# State/lock/log live with the INSTALL (REPO_ROOT == ~/.magepilot for installer-managed
# clones), matching the wrapper's RUNDIR/LOGDIR pattern — never in the user's project.
UPDATE_STATE_FILE = os.path.join(REPO_ROOT, ".magepilot", "update_state.json")
UPDATE_LOCK_FILE = os.path.join(REPO_ROOT, ".magepilot", "update.lock")
UPDATE_LOG_FILE = os.path.join(REPO_ROOT, "logs", "update.log")

# --- Safety: the ONLY bin/magento subcommands the agent may run (read-only) ---
MAGENTO_CLI_WHITELIST = (
    "module:status",
    "setup:db:status",
    "cache:status",
    "indexer:status",
    "dev:di:info",
    "config:show",
    "app:config:status",
)
