"""Filesystem + search tools. Every tool is SANDBOXED to the codebase root (path
traversal outside it is refused). Function bodies are migrated verbatim from
agent/tools.py; only registration moved to the Tool framework.
"""
import os
import re
import shutil
import subprocess
import time

from magepilot import config
from magepilot.index.codebase import get_code_collection
from magepilot.safety.sandbox import _resolve_file, _safe_path
from magepilot.tools.base import Param, RiskLevel, Tool, ToolError, clip as _clip, root_tool


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
def search_code(root: str, query: str, k: int = None) -> str:
    """Semantic search over the indexed codebase. Returns file:line ranges + snippets."""
    col = get_code_collection(root)
    if col.count() == 0:
        return "this project isn't indexed yet — run `magepilot index` first"
    res = col.query(query_texts=[query], n_results=min(k or config.TOP_K_CODE, col.count()))
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


TOOLS = (
    Tool(
        name="search_code", fn=root_tool(search_code), primary="query", risk=RiskLevel.READ,
        params=(Param("query", required=True, description="natural-language query"),
                Param("k", type="integer", description="number of results")),
        description="Semantic search over THIS project's indexed code (app/code only — does NOT see "
                    "vendor/). Input: a natural-language query. Best for 'where/how is X done' in the "
                    "project's own modules.",
    ),
    Tool(
        name="grep", fn=root_tool(grep), primary="pattern", risk=RiskLevel.READ,
        params=(Param("pattern", required=True, description="regex to search for"),
                Param("glob", description="optional filename glob filter")),
        description="Exact/regex text search across ALL files, INCLUDING vendor/ (core & third-party "
                    "Magento). Input: a regex. Magento code uses fully-qualified names, so match the "
                    "bare symbol rather than assuming words are adjacent: to find an implementer use "
                    "'class .*ProductRepository' or 'implements .*ProductRepositoryInterface' (the .* "
                    "spans the `\\Magento\\Catalog\\Api\\` prefix) — NOT 'implements ProductRepositoryInterface'.",
    ),
    Tool(
        name="read_file", fn=root_tool(read_file), primary="path", risk=RiskLevel.READ,
        params=(Param("path", required=True, description="relative file path"),
                Param("start", type="integer", description="first line (1-based)"),
                Param("end", type="integer", description="last line (inclusive)")),
        description="Read a file's contents. Input: {\"path\": \"app/code/...\", \"start\": 1, \"end\": 40} "
                    "(start/end optional, 1-based).",
    ),
    Tool(
        name="find_files", fn=root_tool(find_files), primary="pattern", risk=RiskLevel.READ,
        params=(Param("pattern", required=True, description="glob or substring"),),
        description="Find files by name/glob across the whole project, INCLUDING vendor/. "
                    "Input: a glob like 'di.xml' or '*Repository.php'.",
    ),
    Tool(
        name="list_dir", fn=root_tool(list_dir), primary="path", risk=RiskLevel.READ,
        params=(Param("path", description="relative directory path (default '.')"),),
        description="List a directory. Input: a relative path (default '.').",
    ),
)
