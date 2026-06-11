"""Applying plan operations: previews, sandboxed writes, and the undo journal
(migrated verbatim from agent/edits.py). In Phase 3 the pre-write security scan and
Magento linter hook in here, BEFORE the approval prompt."""
import difflib
import json
import os

from magepilot import config
from magepilot.edits.blocks import _apply_edit
from magepilot.errors import ToolError
from magepilot.safety.sandbox import _safe_path


def preview(root: str, op: dict) -> str:
    """A human-readable preview of one operation (content for new files, diff for edits)."""
    p = op["path"]
    try:
        full = _safe_path(root, p)
    except ToolError as e:
        return f"⛔ refused ({e}): {p}"
    if op["op"] == "mkdir":
        return f"📁 mkdir   {p}"
    if op["op"] == "delete":
        return f"🗑  delete  {p}" + ("" if os.path.isfile(full) else "  (not found — will skip)")
    if op["op"] == "create":
        c = op["content"]
        lines = c.splitlines()
        body = "\n".join("   │ " + ln for ln in lines[:30])
        more = f"\n   │ … (+{len(lines) - 30} more lines)" if len(lines) > 30 else ""
        warn = "  ⚠ OVERWRITES existing file" if os.path.isfile(full) else ""
        return f"📝 create  {p}  ({len(lines)} lines){warn}\n{body}{more}"
    if op["op"] == "edit":
        cur = open(full, encoding="utf-8", errors="replace").read() if os.path.isfile(full) else ""
        new = _apply_edit(cur, op["find"], op["replace"]) if cur else None
        if new is not None:
            diff = "\n".join(difflib.unified_diff(cur.splitlines(), new.splitlines(),
                                                  lineterm="", n=2))[:1500]
            return f"✏️  edit    {p}\n{diff}"
        return f"✏️  edit    {p}  ⚠ target text not found — will skip"
    return f"? {op}"


def apply(root: str, op: dict) -> str:
    """Perform one operation. Caller must have obtained approval. Sandboxed to root."""
    full = _safe_path(root, op["path"])      # raises ToolError if outside the project
    if op["op"] == "mkdir":
        os.makedirs(full, exist_ok=True)
        return f"created directory {op['path']}"
    if op["op"] == "create":
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        content = op["content"]
        if not content.endswith("\n"):
            content += "\n"
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {op['path']}"
    if op["op"] == "delete":
        if os.path.isfile(full):
            os.remove(full)
            return f"deleted {op['path']}"
        return f"skipped (not found): {op['path']}"
    if op["op"] == "edit":
        if not os.path.isfile(full):
            return f"skipped (no such file): {op['path']}"
        cur = open(full, encoding="utf-8", errors="replace").read()
        new = _apply_edit(cur, op["find"], op["replace"])
        if new is None:
            return f"skipped (text not found): {op['path']}"
        with open(full, "w", encoding="utf-8") as f:
            f.write(new)
        return f"edited {op['path']}"
    return f"unknown op: {op.get('op')}"


def _reverse_for(root: str, op: dict):
    """Capture how to undo `op` — computed BEFORE it's applied, from the current state."""
    full = _safe_path(root, op["path"])
    if op["op"] == "create":
        if os.path.isfile(full):
            return {"undo": "restore", "path": op["path"],
                    "content": open(full, encoding="utf-8", errors="replace").read()}
        return {"undo": "remove", "path": op["path"]}
    if op["op"] in ("edit", "delete"):
        return {"undo": "restore", "path": op["path"],
                "content": open(full, encoding="utf-8", errors="replace").read()} if os.path.isfile(full) else None
    if op["op"] == "mkdir":
        return None if os.path.isdir(full) else {"undo": "rmdir", "path": op["path"]}
    return None


def _save_journal(root: str, reverses: list) -> None:
    os.makedirs(os.path.dirname(config.UNDO_FILE), exist_ok=True)
    with open(config.UNDO_FILE, "w", encoding="utf-8") as f:
        json.dump({"root": os.path.realpath(root), "ops": [r for r in reverses if r]}, f)


# Directories undo must NEVER remove (Magento structural + system/generated folders), even if empty.
_PROTECT_RELPATHS = {
    ".", "app", "app/code", "app/design", "app/design/frontend", "app/design/adminhtml",
    "app/etc", "app/i18n", "lib", "generated", "var", "pub", "pub/static", "pub/media",
    "dev", "setup", "vendor", "node_modules", ".git",
}
_PROTECT_NAMES = {"generated", "var", "pub", "dev", "setup", "vendor", "node_modules", ".git"}


def _cleanup_empty_dirs(root: str, removed_paths: list) -> int:
    """Remove directories left EMPTY by undoing creates — walking up from each removed file,
    stopping at structural/system dirs, non-empty dirs, or the project root. Never touches
    generated/var/pub/dev/setup/vendor or the standard app/code, app/design roots."""
    root_real = os.path.realpath(root)
    removed = 0
    for p in removed_paths:
        try:
            d = os.path.dirname(_safe_path(root, p))
        except ToolError:
            continue
        while True:
            d_real = os.path.realpath(d)
            if d_real == root_real or not d_real.startswith(root_real + os.sep):
                break
            rel = os.path.relpath(d_real, root_real).replace("\\", "/")
            if rel in _PROTECT_RELPATHS or os.path.basename(rel) in _PROTECT_NAMES:
                break
            try:
                if os.path.isdir(d_real) and not os.listdir(d_real):
                    os.rmdir(d_real)
                    print(f"   ↩ removed empty dir {rel}")
                    removed += 1
                    d = os.path.dirname(d_real)
                else:
                    break       # not empty (has other code) or already gone → stop
            except OSError:
                break
    return removed


def undo(root: str = None) -> int:
    """Revert the last `make`. Restores/removes the files it touched. Returns how many were reverted."""
    try:
        journal = json.load(open(config.UNDO_FILE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("nothing to undo — no recorded `make`.")
        return 0
    root = root or journal.get("root")
    if not root:
        print("nothing to undo.")
        return 0
    n = 0
    removed_files = []
    for rev in reversed(journal.get("ops", [])):     # reverse order: inner files before their dirs
        try:
            full = _safe_path(root, rev["path"])
        except ToolError:
            continue
        if rev["undo"] == "restore" and rev.get("content") is not None:
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(rev["content"])
            print(f"   ↩ restored {rev['path']}"); n += 1
        elif rev["undo"] == "remove" and os.path.isfile(full):
            os.remove(full); print(f"   ↩ removed {rev['path']}"); n += 1
            removed_files.append(rev["path"])
        elif rev["undo"] == "rmdir":
            try:
                os.rmdir(full); print(f"   ↩ removed dir {rev['path']}"); n += 1
            except OSError:
                pass                                   # directory not empty — leave it
    n += _cleanup_empty_dirs(root, removed_files)      # tidy up now-empty dirs we created
    try:
        os.remove(config.UNDO_FILE)                    # one level of undo
    except OSError:
        pass
    print(f"undid {n} change(s)." if n else "nothing to revert.")
    return n
