"""Write-capable, approval-gated file changes — Magepilot can create/edit/delete files in your
project, but ONLY after you approve each change. Everything is sandboxed to the project root.

The model returns a plan in a simple, parse-robust block format (no JSON escaping needed):

    @@MKDIR app/design/frontend/Vendor/Demo
    @@END

    @@CREATE app/design/frontend/Vendor/Demo/registration.php
    <full file content>
    @@END

    @@EDIT path/to/file.php
    @@FIND
    <exact text to replace>
    @@REPLACE
    <new text>
    @@END

    @@DELETE path/to/file.php
    @@END
"""
import difflib
import json
import os
import re
import textwrap
import urllib.error
import urllib.request

from agent import config
from agent._progress import Spinner
from agent.tools import _safe_path, _resolve_file, ToolError

_OP = re.compile(r"^@@(CREATE|MKDIR|DELETE|EDIT)[ \t]+(.+?)\s*$")

SYSTEM = (
    "You are a Magento 2 + Hyvä scaffolding engine. Turn the user's request into a precise list of "
    "file operations, using EXACTLY this block format and NOTHING else — no prose, no ``` fences:\n\n"
    "@@MKDIR <relative/dir>\n@@END\n\n"
    "@@CREATE <relative/path>\n<the COMPLETE file content>\n@@END\n\n"
    "@@EDIT <relative/path>\n@@FIND\n<exact text already in the file>\n@@REPLACE\n<new text>\n@@END\n\n"
    "@@DELETE <relative/path>\n@@END\n\n"
    "Rules: modern, idiomatic Magento 2 + Hyvä. Use standard paths "
    "(app/code/Vendor/Module/..., app/design/frontend/Vendor/Theme/...). Include EVERY file the task "
    "needs (registration.php, etc.). Put complete contents in @@CREATE.\n"
    "A Hyvä theme needs only registration.php and theme.xml — it inherits Tailwind + Alpine from its "
    "parent. Hyvä uses TAILWIND, never Less: do NOT create .less files.\n"
    "Do NOT wrap anything in ``` markdown fences. End every block with @@END and write NOTHING after the "
    "final @@END.\n"
    "When the user wants to change an EXISTING file whose current content is shown above, use @@EDIT and "
    "copy the EXACT existing text into @@FIND (verbatim, enough to be unique) and the new text into "
    "@@REPLACE. Prefer @@EDIT over @@CREATE for files that already exist."
)


def _extract(task: str):
    """Best-effort pull of (vendor, name) from a request so we don't ask when they're already given."""
    vendor = name = None
    m = re.search(r"\b([A-Z][A-Za-z0-9]+)[\\_]([A-Z][A-Za-z0-9]+)\b", task)   # Vendor_Module / Vendor\Module
    if m:
        vendor, name = m.group(1), m.group(2)
    if not vendor:
        m = re.search(r"\bvendor[\s:_]+([A-Za-z][A-Za-z0-9]+)", task, re.I)
        if m:
            vendor = m.group(1)
    if not name:
        m = re.search(r"\b(?:name|named|called)[\s:]+([A-Za-z][A-Za-z0-9]+)", task, re.I)
        if m:
            name = m.group(1)
    return vendor, name


def clarify(task: str, asker) -> str:
    """For 'create a module/theme' requests, ask for vendor/name if they're missing, then augment the task."""
    m = re.search(r"\b(module|theme)\b", task, re.I)
    if not m or not re.search(r"\b(create|add|generate|scaffold|build|new|make|set ?up)\b", task, re.I):
        return task
    artifact = m.group(1).lower()
    vendor, name = _extract(task)
    extra = []
    if not vendor:
        v = (asker("  Vendor name? ") or "").strip()
        if v:
            extra.append(f"Vendor: {v}")
    if not name:
        n = (asker(f"  {artifact.capitalize()} name (e.g. Blog)? ") or "").strip()
        if n:
            extra.append(f"{artifact.capitalize()} name: {n}")
    return task + ("\n" + "\n".join(extra) if extra else "")


def _context(task: str, root: str) -> str:
    """If the task references existing files, include their current content so edits use exact text."""
    blocks, seen = [], set()
    for tok in re.findall(r"[\w./\\-]+\.(?:php|phtml|xml|js|less|css|graphqls|json)", task):
        full = _resolve_file(root, tok)
        if not full or full in seen:
            continue
        seen.add(full)
        rel = os.path.relpath(full, os.path.realpath(root))
        try:
            body = open(full, encoding="utf-8", errors="replace").read()[:4000]
        except OSError:
            continue
        blocks.append(f"=== current content of {rel} (edit this file in place) ===\n{body}")
        if len(blocks) >= 4:
            break
    return "\n\n".join(blocks)


def _strip_fences(s: str) -> str:
    """Drop ``` / ~~~ markdown fence lines (models sometimes wrap FIND/REPLACE in them)."""
    return "\n".join(ln for ln in s.splitlines() if not re.match(r"^\s*(```|~~~)", ln)).strip("\n")


def _clean(content: str) -> str:
    """Strip markdown fences and any trailing 'Why:' explanation the model may leak into a file."""
    out = []
    for ln in content.splitlines():
        if re.match(r"^\s*(```|~~~)", ln):      # drop fence lines
            continue
        if re.match(r"^\s*Why:\s", ln):         # model's explanation leaked in — stop here
            break
        out.append(ln)
    return "\n".join(out).strip()


def _magento_model() -> str:
    """Use the Magento fine-tune for scaffolding (idiomatic content)."""
    try:
        with urllib.request.urlopen(config.MODEL_SERVER + "/models", timeout=5) as r:
            ids = [m["id"] for m in json.load(r)["data"]]
        return next((m for m in ids if "magento" in m.lower()), ids[0])
    except Exception:
        return config.MODEL_MATCH


def generate_plan(task: str, root: str) -> list[dict]:
    ctx = _context(task, root)
    user = f"{ctx}\n\n{task}" if ctx else task
    payload = {"model": _magento_model(),
               "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
               "stop": ["<|im_end|>"], **config.SAMPLING}
    req = urllib.request.Request(config.MODEL_SERVER + "/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with Spinner("planning changes"):
            with urllib.request.urlopen(req, timeout=300) as r:
                text = json.load(r)["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError) as e:
        print(f"⚠ can't reach the model server at {config.MODEL_SERVER} — run `magepilot serve` first.\n   ({e})")
        return []
    return parse_plan(text.split("<|im_end|>")[0])


def parse_plan(text: str) -> list[dict]:
    """Parse @@-block operations into a list of {op, path, ...} dicts."""
    ops, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        m = _OP.match(lines[i].strip())
        if not m:
            i += 1
            continue
        kind, path = m.group(1).lower(), m.group(2).strip()
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() != "@@END":
            body.append(lines[i])
            i += 1
        i += 1  # consume @@END
        if kind == "create":
            ops.append({"op": "create", "path": path, "content": _clean("\n".join(body))})
        elif kind == "mkdir":
            ops.append({"op": "mkdir", "path": path})
        elif kind == "delete":
            ops.append({"op": "delete", "path": path})
        elif kind == "edit":
            fm = re.search(r"@@FIND\n(.*?)\n@@REPLACE\n(.*)", "\n".join(body), re.S)
            ops.append({"op": "edit", "path": path,
                        "find": _strip_fences(fm.group(1)) if fm else "",
                        "replace": _strip_fences(fm.group(2)) if fm else ""})
    # drop duplicate create/mkdir/delete on the same path (model sometimes repeats)
    seen, deduped = set(), []
    for o in ops:
        key = (o["op"], o["path"])
        if o["op"] in ("create", "mkdir", "delete") and key in seen:
            continue
        seen.add(key)
        deduped.append(o)
    return deduped


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


def _apply_edit(text: str, find: str, replace: str):
    """Replace `find` with `replace`, tolerant of indentation/whitespace, re-indenting the
    replacement to match where it lands. Returns the new text, or None if `find` can't be located."""
    if not find:
        return None
    if find in text:
        span = (text.index(find), text.index(find) + len(find))
    else:                                   # whitespace-tolerant: match the tokens, any spacing between
        toks = [re.escape(t) for t in find.split()]
        if not toks:
            return None
        m = re.search(r"\s+".join(toks), text)
        if not m:
            return None
        span = (m.start(), m.end())
    line_start = text.rfind("\n", 0, span[0]) + 1
    lead = text[line_start:span[0]]
    if lead.strip() == "":                  # match begins at the line's indentation → absorb + reuse it
        indent, start = lead, line_start
    else:
        indent, start = "", span[0]
    body = textwrap.dedent(replace)
    reindented = "\n".join((indent + ln if ln.strip() else ln) for ln in body.splitlines())
    return text[:start] + reindented + text[span[1]:]


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


def run_make(task: str, root: str, approver=None, asker=None,
             auto: bool = False, plan_only: bool = False) -> dict:
    """Generate a file-change plan for `task`, preview each, get approval, and apply.

    `asker(question) -> answer` is used to fill in missing details (e.g. vendor) before planning.
    `approver(op) -> "yes" | "no" | "all"`. Returns {"applied": [...], "skipped": [...]}.
    """
    if asker is not None:
        task = clarify(task, asker)
    ops = generate_plan(task, root)
    if not ops:
        print("No file changes were produced. Try rephrasing, or ask it as a question instead.")
        return {"applied": [], "skipped": []}

    print(f"\nProposed {len(ops)} change(s) in {root}:\n")
    applied, skipped, reverses = [], [], []
    approve_all = auto
    for op in ops:
        print(preview(root, op) + "\n")
        if plan_only:
            continue
        ok = approve_all
        if not ok and approver:
            d = approver(op)
            if d == "all":
                approve_all = ok = True   # apply this and every remaining change
            elif d == "yes":
                ok = True
        if ok:
            try:
                rev = _reverse_for(root, op)          # capture undo BEFORE changing anything
                print("   ↳ " + apply(root, op) + "\n")
                applied.append(op)
                reverses.append(rev)
            except ToolError as e:
                print(f"   ↳ ⛔ refused: {e}\n"); skipped.append(op)
            except Exception as e:
                print(f"   ↳ error: {e}\n"); skipped.append(op)
        else:
            print("   ↳ skipped\n"); skipped.append(op)

    if applied and not plan_only:
        _save_journal(root, reverses)
        print("(run /undo to revert this)\n")
        from agent.suggest import suggest
        cmds = suggest([o["path"] for o in applied])
        if cmds:
            print("These Magento commands are needed next:")
            for c in cmds:
                print(f"   bin/magento {c['command']}   ({c['reason']})")
            if asker and not auto and (asker("\nRun them now? [y/N] ") or "").strip().lower() in ("y", "yes"):
                from agent.actions import execute
                for c in cmds:
                    print(f"\n$ bin/magento {c['command']}")
                    res = execute(root, c["command"], approver=lambda cmd, sub: "yes")
                    if res["ran"]:
                        out = "\n".join("   " + ln for ln in (res.get("output") or "").splitlines()[:40])
                        print(f"   (exit {res['exit']})\n{out}")
                    else:
                        print(f"   skipped — {res['reason']}")
    return {"applied": applied, "skipped": skipped}
