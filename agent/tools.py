"""#2 Tools the agent can call. Every filesystem tool is SANDBOXED to the codebase root
(path traversal outside it is refused), and `magento_cli` only runs a read-only whitelist.

A tool returns a short string (the "observation"). Tools never raise to the loop — they
catch and return an error string so one bad call can't crash the agent.
"""
import json
import os
import re
import shutil
import subprocess
import time

import chromadb
from chromadb.utils import embedding_functions

from agent import config
from agent.codebase_index import get_code_collection


class ToolError(Exception):
    """Raised inside a tool; callers convert it to an observation string."""


# --------------------------------------------------------------------------- sandbox
def _safe_path(root: str, rel: str) -> str:
    """Resolve `rel` under `root`, refusing any path that escapes the root."""
    root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root, rel or "."))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise ToolError(f"path '{rel}' is outside the codebase root (refused)")
    return candidate


def _clip(text: str, limit: int = config.MAX_OBS_CHARS) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} more chars]"


def _resolve_file(root: str, path: str) -> str | None:
    """Resolve a file inside root, tolerant of guessed prefixes (e.g. 'app/code/').

    Tries the exact sandboxed path first, then a unique suffix/basename match under root.
    Only ever returns paths INSIDE root (the walk never leaves it), so the sandbox holds.
    Returns None for no-match or an ambiguous (multi-file) basename.
    """
    try:
        exact = _safe_path(root, path)
        if os.path.isfile(exact):
            return exact
    except ToolError:
        pass

    norm = (path or "").replace("\\", "/").lstrip("/")
    for prefix in ("app/code/", "app/", "./"):
        if norm.startswith(prefix):
            norm = norm[len(prefix):]
    base = os.path.basename(norm)
    root_real = os.path.realpath(root)
    suffix_hits, base_hits = [], []
    for dp, dn, fn in os.walk(root_real):
        dn[:] = [d for d in dn if d not in config.SEARCH_SKIP_DIRS and not d.startswith(".")]
        for name in fn:
            rel = os.path.relpath(os.path.join(dp, name), root_real).replace("\\", "/")
            if rel == norm or rel.endswith("/" + norm):
                suffix_hits.append(os.path.join(dp, name))
            elif name == base:
                base_hits.append(os.path.join(dp, name))
    hits = suffix_hits or base_hits
    return hits[0] if len(hits) == 1 else None


# --------------------------------------------------------------------------- file tools
def read_file(root: str, path: str, start: int = None, end: int = None) -> str:
    """Read a file (optionally a 1-based inclusive line range) from inside the codebase."""
    full = _resolve_file(root, path)
    if not full:
        raise ToolError(f"not a file: '{path}' (no unique match under the codebase root — "
                        f"try find_files first)")
    rel = os.path.relpath(full, os.path.realpath(root))
    with open(full, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    if start is not None or end is not None:
        s = max(1, int(start or 1))
        e = min(len(lines), int(end or len(lines)))
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(s, e + 1))
        head = f"{rel} (lines {s}-{e} of {len(lines)})"
    else:
        body = "\n".join(f"{i:>5}  {ln}" for i, ln in enumerate(lines, 1))
        head = f"{rel} ({len(lines)} lines)"
    return _clip(f"{head}\n{body}")


def list_dir(root: str, path: str = ".") -> str:
    """List a directory inside the codebase (dirs get a trailing slash)."""
    full = _safe_path(root, path)
    if not os.path.isdir(full):
        raise ToolError(f"not a directory: {path}")
    entries = []
    for name in sorted(os.listdir(full)):
        if name.startswith("."):
            continue
        entries.append(name + ("/" if os.path.isdir(os.path.join(full, name)) else ""))
    return _clip(f"{path}/\n" + "\n".join(entries) if entries else f"{path}/ (empty)")


def find_files(root: str, pattern: str) -> str:
    """Find files whose path matches a glob/substring (e.g. 'di.xml', '*Repository.php')."""
    import fnmatch
    pat = pattern.strip()
    hits = []
    for dirpath, dirnames, filenames in os.walk(_safe_path(root, ".")):
        dirnames[:] = [d for d in dirnames if d not in config.SEARCH_SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat) or pat in rel:
                hits.append(rel)
        if len(hits) >= 100:
            break
    hits.sort()
    if not hits:
        return f"no files match '{pattern}'"
    return _clip(f"{len(hits)} file(s) matching '{pattern}':\n" + "\n".join(hits[:100]))


def grep(root: str, pattern: str, glob: str = None) -> str:
    """Literal/regex search for exact symbols across the codebase (ripgrep if available)."""
    root_dir = _safe_path(root, ".")
    rg = shutil.which("rg")
    if rg:
        # --no-ignore-vcs so a .gitignore'd vendor/ is still searched (that's where core Magento
        # lives); then glob out the noisy generated dirs so we get vendor without var/pub/generated.
        cmd = [rg, "--line-number", "--no-heading", "--color", "never", "--max-count", "50",
               "--no-ignore-vcs", "-e", pattern]
        for d in config.SEARCH_SKIP_DIRS:
            cmd += ["--glob", f"!**/{d}/**"]
        if glob:
            cmd += ["--glob", glob]
        cmd.append(root_dir)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
        except (subprocess.SubprocessError, OSError) as e:
            return f"grep failed: {e}"
        out = "\n".join(os.path.relpath(line, root) if line.startswith(root_dir) else line
                        for line in out.splitlines())
        return _clip(out) if out.strip() else f"no matches for '{pattern}'"

    # Fallback: pure-Python walk + regex.
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"invalid regex: {e}"
    results, count = [], 0
    # vendor/ can be tens of thousands of files; bound a no-match walk so the agent never hangs.
    deadline = time.monotonic() + 15
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in config.SEARCH_SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if glob:
                import fnmatch
                if not fnmatch.fnmatch(name, glob):
                    continue
            if not name.endswith(config.INDEX_EXTENSIONS):
                continue
            full = os.path.join(dirpath, name)
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            rel = os.path.relpath(full, root)
                            results.append(f"{rel}:{i}:{line.rstrip()}")
                            count += 1
                            if count >= 50:
                                break
            except OSError:
                continue
        if count >= 50 or time.monotonic() > deadline:
            break
    return _clip("\n".join(results)) if results else f"no matches for '{pattern}'"


# --------------------------------------------------------------------------- semantic search
def search_code(root: str, query: str, k: int = config.TOP_K_CODE) -> str:
    """Semantic search over the indexed codebase. Returns file:line ranges + snippets."""
    col = get_code_collection(root)
    if col.count() == 0:
        return "this project isn't indexed yet — run `magepilot index` first"
    res = col.query(query_texts=[query], n_results=min(k, col.count()))
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    if not docs:
        return f"no code found for '{query}'"
    blocks = []
    for d, m, dist in zip(docs, metas, dists):
        snippet = "\n".join(d.splitlines()[:22])
        blocks.append(f"--- {m.get('rel')}:{m.get('start')}-{m.get('end')} (dist {dist:.3f})\n{snippet}")
    return _clip("\n".join(blocks))


def kb_search(root: str, query: str, k: int = config.TOP_K_KB) -> str:
    """Search the curated Magento knowledge base (facts/APIs) — separate from the code index."""
    try:
        client = chromadb.PersistentClient(path=config.KB_CHROMA_PATH)
        ef = embedding_functions.DefaultEmbeddingFunction()
        col = client.get_or_create_collection(config.KB_COLLECTION, embedding_function=ef)
    except Exception as e:
        return f"knowledge base unavailable: {e}"
    if col.count() == 0:
        return "knowledge base is empty (run `python rag/ingest.py`)"
    res = col.query(query_texts=[query], n_results=min(k, col.count()))
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    if not docs:
        return f"no knowledge found for '{query}'"
    # Never expose the knowledge-base filename (private datasource): label by title only,
    # falling back to a generic heading rather than the .md source.
    blocks = [f"--- {m.get('title') or 'Magento reference'}\n{d}" for d, m in zip(docs, metas)]
    return _clip("\n\n".join(blocks))


# --------------------------------------------------------------------------- read-only sql (debugging)
def sql_query(root: str, query: str) -> str:
    """Run a READ-ONLY SQL query against the store DB. Writes/DDL are refused."""
    from agent.db import run_query
    r = run_query(root, query)
    return _clip(r["output"]) if r["ok"] else f"error: {r['error']}"


# --------------------------------------------------------------------------- magento cli (read-only)
_CLI_SAFE = re.compile(r"^[A-Za-z0-9:_\-./= ]+$")


def magento_cli(root: str, command: str) -> str:
    """Run a READ-ONLY bin/magento command from the whitelist (e.g. 'module:status')."""
    command = (command or "").strip().removeprefix("bin/magento").removeprefix("magento").strip()
    if not _CLI_SAFE.match(command):
        raise ToolError("command rejected: illegal characters (shell metacharacters not allowed)")
    sub = command.split(" ", 1)[0]
    if sub not in config.MAGENTO_CLI_WHITELIST:
        raise ToolError(
            f"command '{sub}' is not allowed. Read-only whitelist: "
            + ", ".join(config.MAGENTO_CLI_WHITELIST)
        )
    bin_magento = _safe_path(root, "bin/magento")
    if not os.path.isfile(bin_magento):
        return "no Magento root configured (bin/magento not found under the codebase root)"
    try:
        out = subprocess.run(
            ["php", bin_magento, *command.split(" ")],
            capture_output=True, text=True, timeout=120, cwd=root,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return f"bin/magento failed: {e}"
    return _clip((out.stdout + out.stderr).strip() or "(no output)")


# --------------------------------------------------------------------------- registry + dispatch
TOOLS = {
    "search_code": {
        "func": search_code, "primary": "query",
        "desc": "Semantic search over THIS project's indexed code (app/code only — does NOT see "
                "vendor/). Input: a natural-language query. Best for 'where/how is X done' in the "
                "project's own modules.",
    },
    "grep": {
        "func": grep, "primary": "pattern",
        "desc": "Exact/regex text search across ALL files, INCLUDING vendor/ (core & third-party "
                "Magento). Input: a regex. Magento code uses fully-qualified names, so match the "
                "bare symbol rather than assuming words are adjacent: to find an implementer use "
                "'class .*ProductRepository' or 'implements .*ProductRepositoryInterface' (the .* "
                "spans the `\\Magento\\Catalog\\Api\\` prefix) — NOT 'implements ProductRepositoryInterface'.",
    },
    "read_file": {
        "func": read_file, "primary": "path",
        "desc": "Read a file's contents. Input: {\"path\": \"app/code/...\", \"start\": 1, \"end\": 40} "
                "(start/end optional, 1-based).",
    },
    "find_files": {
        "func": find_files, "primary": "pattern",
        "desc": "Find files by name/glob across the whole project, INCLUDING vendor/. "
                "Input: a glob like 'di.xml' or '*Repository.php'.",
    },
    "list_dir": {
        "func": list_dir, "primary": "path",
        "desc": "List a directory. Input: a relative path (default '.').",
    },
    "kb_search": {
        "func": kb_search, "primary": "query",
        "desc": "Look up Magento facts/APIs in the curated knowledge base (not your code). "
                "Input: a question like 'how does a plugin differ from a preference'.",
    },
    "magento_cli": {
        "func": magento_cli, "primary": "command",
        "desc": "Run a READ-ONLY bin/magento command from a safe whitelist "
                "(module:status, dev:di:info, cache:status, indexer:status, setup:db:status, config:show).",
    },
    "sql_query": {
        "func": sql_query, "primary": "query",
        "desc": "Run a READ-ONLY SQL query against the store database for debugging "
                "(SELECT / SHOW / DESCRIBE only — writes refused). Input: a SQL string. Use your "
                "knowledge of Magento's schema (catalog_product_entity, sales_order, eav_attribute, "
                "*_index tables, core_config_data, etc.).",
    },
}


def tool_catalog() -> str:
    """Human-readable tool list for the system prompt."""
    return "\n".join(f"- {name}: {meta['desc']}" for name, meta in TOOLS.items())


def run_tool(root: str, name: str, arg: str) -> str:
    """Dispatch one tool call. `arg` is the raw 'Action Input' (JSON object or a plain string)."""
    name = (name or "").strip()
    if name not in TOOLS:
        return f"unknown tool '{name}'. Available: {', '.join(TOOLS)}"
    meta = TOOLS[name]
    kwargs = _parse_args(arg, meta["primary"])
    try:
        return meta["func"](root, **kwargs)
    except ToolError as e:
        return f"error: {e}"
    except TypeError as e:
        return f"error: bad arguments for {name} ({e})"
    except Exception as e:  # never let a tool crash the loop
        return f"error: {name} failed: {e}"


def _parse_args(arg: str, primary: str) -> dict:
    """Accept either a JSON object ({\"path\": ...}) or a plain string (mapped to the primary arg)."""
    arg = (arg or "").strip()
    if not arg:
        return {}
    if arg[0] == "{":
        try:
            obj = json.loads(arg)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {primary: arg.strip().strip('"').strip("'")}
