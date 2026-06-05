"""Central configuration for the Magepilot agent."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

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
