"""PHP/Magento stack-trace parsing and var/log readers — pure text processing, no model.

Handles the formats Magento actually produces:
  #0 /abs/path/File.php(123): Class->method()           (exception traces)
  ... thrown in /abs/path/File.php on line 123
  main.CRITICAL: ...: Error: msg in /abs/path/File.php:123   (exception.log entries)
  Fatal error: Uncaught TypeError: ... in /path/File.php:123 (CLI / DI compile)

The analysis flags the first app/code frame as the most likely culprit (your code beats
vendor code as a suspect), relativizes every path against the project root, and detects
DI/interceptor signatures so the agent reaches for the graph tools instead of grep.
"""
import os
import re

from magepilot.tools.base import ToolError, clip as _clip

_FRAME_RE = re.compile(r"^#\d+\s+(\S+?\.php)\((\d+)\):\s*(.+?)\s*$", re.M)
_THROWN_RE = re.compile(r"thrown in\s+(\S+?\.php) on line (\d+)")
_IN_RE = re.compile(r"\bin\s+(\S+?\.php)(?::|\(| on line )(\d+)\)?")
_EXC_RE = re.compile(
    r"(?:Uncaught\s+)?((?:[A-Z][\w]*\\)*[A-Z][\w]*(?:Exception|Error))\b[:\s]*([^\n]*)")

# DI / interception signatures → the graph tools are the right next move
_DI_HINTS = (
    ("Interceptor", "a generated Interceptor is involved — inspect plugins with the "
                    "`wiring`/`diagnose_plugin` tools, not the generated file"),
    ("ObjectManager", "a DI resolution failure — check di.xml wiring with the `wiring` "
                      "tool and constructor signatures with `symbol`"),
    ("does not exist", "a class is referenced but missing — `symbol` the name to see "
                       "where it should live; check di.xml typos and run setup:di:compile"),
    ("Missing required argument", "constructor args don't match di.xml — compare "
                                  "`symbol <class>` injections against the di.xml entry"),
    ("Source class", "code generation refers to a non-existent source class — usually a "
                     "Factory/Proxy suffix typo in di.xml or a missing module"),
)

LOGS = ("exception.log", "system.log", "debug.log", "cron.log")


def parse_trace(text: str) -> dict:
    """{'exception', 'message', 'frames': [{'file','line','call'}]} — best effort."""
    text = text or ""
    exception = message = None
    m = _EXC_RE.search(text)
    if m:
        exception, message = m.group(1), m.group(2).strip().rstrip(":")
    frames = [{"file": f, "line": int(ln), "call": call.strip()}
              for f, ln, call in _FRAME_RE.findall(text)]
    for f, ln in _THROWN_RE.findall(text):
        frames.insert(0, {"file": f, "line": int(ln), "call": "(throw site)"})
    if not frames:
        frames = [{"file": f, "line": int(ln), "call": ""}
                  for f, ln in _IN_RE.findall(text)]
    seen, deduped = set(), []
    for fr in frames:
        key = (fr["file"], fr["line"])
        if key not in seen:
            seen.add(key)
            deduped.append(fr)
    return {"exception": exception, "message": message, "frames": deduped}


def _relativize(root: str, path: str) -> str:
    rp = os.path.realpath(root) + os.sep
    if path.startswith(rp):
        return path[len(rp):]
    # absolute path from another machine/container: salvage from app/ or vendor/
    m = re.search(r"/((?:app|vendor|generated)/.+)$", path)
    return m.group(1) if m else path


def analyze(root: str, trace: str) -> str:
    """The stack_trace tool body: parsed, relativized, culprit-flagged, hinted."""
    parsed = parse_trace(trace)
    if not parsed["frames"] and not parsed["exception"]:
        return ("no PHP stack frames recognized — paste the full error including the "
                "'#0 …' lines, or use magento_logs to pull the latest exception")
    lines = []
    if parsed["exception"]:
        lines.append(f"exception: {parsed['exception']}")
    if parsed["message"]:
        lines.append(f"message: {parsed['message'][:300]}")
    culprit = None
    out_frames = []
    for fr in parsed["frames"][:14]:
        rel = _relativize(root, fr["file"])
        mark = ""
        if culprit is None and rel.startswith("app/code/"):
            culprit = f"{rel}:{fr['line']}"
            mark = "   ← most likely culprit (your code)"
        out_frames.append(f"  {rel}:{fr['line']}  {fr['call'][:80]}{mark}")
    if culprit is None and parsed["frames"]:
        fr = parsed["frames"][0]
        culprit = f"{_relativize(root, fr['file'])}:{fr['line']}"
        out_frames[0] += "   ← first frame (no app/code frame found)"
    lines.append(f"frames ({len(parsed['frames'])}):")
    lines += out_frames
    if culprit:
        lines.append(f"next: read_file {culprit.rsplit(':', 1)[0]} around line "
                     f"{culprit.rsplit(':', 1)[1]}")
    blob = trace + " " + (parsed["message"] or "")
    for needle, hint in _DI_HINTS:
        if needle.lower() in blob.lower():
            lines.append(f"hint: {hint}")
            break
    return _clip("\n".join(lines))


def recent_log_entries(root: str, log: str = "exception.log", n: int = 2) -> str:
    """The last n entries of a var/log file (entries delimited by [timestamp] lines)."""
    log = (log or "exception.log").strip().lstrip("/")
    if log not in LOGS:
        raise ToolError(f"unknown log '{log}' — one of: {', '.join(LOGS)}")
    path = os.path.join(os.path.realpath(root), "var", "log", log)
    if not os.path.isfile(path):
        return f"var/log/{log} does not exist under this root"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))     # the tail is where the news is
            tail = f.read()
    except OSError as e:
        return f"cannot read var/log/{log}: {e}"
    starts = [m.start() for m in re.finditer(r"^\[\d{4}-\d{2}-\d{2}T", tail, re.M)]
    if not starts:
        return _clip(tail[-3000:] or f"var/log/{log} is empty")
    n = max(1, min(int(n or 2), 5))
    entries = tail[starts[-n]:]
    return _clip(f"last {n} entr{'y' if n == 1 else 'ies'} of var/log/{log}:\n{entries}")
