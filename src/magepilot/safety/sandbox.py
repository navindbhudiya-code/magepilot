"""Path sandbox — every filesystem operation resolves through here and is confined to
the codebase root (migrated verbatim from agent/tools.py)."""
import os

from magepilot import config
from magepilot.errors import ToolError


def _safe_path(root: str, rel: str) -> str:
    """Resolve `rel` under `root`, refusing any path that escapes the root."""
    root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root, rel or "."))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise ToolError(f"path '{rel}' is outside the codebase root (refused)")
    return candidate


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
