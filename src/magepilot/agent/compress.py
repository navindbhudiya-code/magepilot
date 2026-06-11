"""Context compression for an 8–16k window.

The discipline (docs/architecture/03): tool observations are clipped at source; the
executor scratchpad is folded mid-task when it grows; the whole scratchpad dies at the
task boundary, surviving only as a ≤120-token note. The cardinal rule, enforced here in
code: **file paths and line numbers are never paraphrased** — they are extracted verbatim
by regex before any summarization and re-attached after, because a 7B will otherwise
corrupt them.

Every function degrades deterministically when no model is reachable — compression must
never be the thing that kills a run.
"""
import re

from magepilot.llm.router import get_router

# file paths with optional :line / :start-end — extracted verbatim, never paraphrased
_PATH_RE = re.compile(
    r"[\w][\w./\\-]*\.(?:php|phtml|xml|js|graphqls|less|css|html|csv|json)(?::\d+(?:-\d+)?)?")

NOTE_MAX_CHARS = 480           # ≈120 tokens
FOLD_THRESHOLD = 6000          # fold the scratchpad once it exceeds this many chars


def extract_paths(text: str, limit: int = 8) -> list[str]:
    """Unique file[:line] references in order of first appearance."""
    seen, out = set(), []
    for m in _PATH_RE.finditer(text or ""):
        p = m.group(0)
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= limit:
            break
    return out


def _summarize(prompt: str, fallback: str, max_tokens: int = 220) -> str:
    """One summarizer-role call; on ANY failure return the deterministic fallback."""
    try:
        out = get_router().complete(
            "summarizer",
            [{"role": "system",
              "content": "You compress agent investigation notes. Reply with ONLY the "
                         "compressed text — no preamble. Copy file paths and line numbers "
                         "EXACTLY as given; never invent or alter them."},
             {"role": "user", "content": prompt}],
            stop=["<|im_end|>"], sampling={"max_tokens": max_tokens}, timeout=120,
        ).strip()
        return out or fallback
    except Exception:
        return fallback


def task_note(goal: str, text: str) -> str:
    """Compress a finished task's output into a ≤120-token note. Verbatim paths win:
    they are re-attached after summarization even if the model dropped them."""
    text = (text or "").strip()
    paths = extract_paths(text)
    fallback = text[:NOTE_MAX_CHARS]
    note = fallback if len(text) <= NOTE_MAX_CHARS else _summarize(
        f"Task: {goal}\n\nFindings:\n{text[:4000]}\n\n"
        f"Compress into at most 3 short sentences of durable facts.", fallback)
    note = note[:NOTE_MAX_CHARS]
    missing = [p for p in paths if p not in note]
    if missing:
        note += "\n  refs: " + ", ".join(missing)
    return note


def fold_scratchpad(scratchpad: str, threshold: int = FOLD_THRESHOLD) -> str:
    """Mid-task fold: compress the OLDEST steps into a findings block, keep the newest
    steps verbatim. No-op below the threshold."""
    if len(scratchpad) <= threshold:
        return scratchpad
    keep_from = scratchpad.rfind("Thought:", 0, len(scratchpad) - threshold // 2)
    if keep_from <= 0:
        return scratchpad[-threshold:]
    old, recent = scratchpad[:keep_from], scratchpad[keep_from:]
    paths = extract_paths(old, limit=12)
    fallback = "(earlier steps compressed)" + (
        " refs: " + ", ".join(paths) if paths else "")
    folded = _summarize(
        f"Compress these earlier investigation steps into at most 5 lines of findings, "
        f"keeping every file path and line number verbatim:\n\n{old[:5000]}",
        fallback, max_tokens=300)
    missing = [p for p in paths if p not in folded]
    if missing:
        folded += "\n  refs: " + ", ".join(missing)
    return f"(Findings from earlier steps:)\n{folded}\n\n{recent}"


def summarize_run(objective: str, state) -> str:
    """The final answer shown to the user, synthesized from task notes (FINISH stage)."""
    notes = state.notes_block()
    fallback_lines = [f"Objective: {objective}"]
    for t in state.plan:
        mark = {"done": "✓", "skipped": "→ skipped", "failed": "✗"}.get(t.status, "·")
        fallback_lines.append(f"{mark} [{t.kind}] {t.goal}" + (f" — {t.note}" if t.note else ""))
    fallback = "\n".join(fallback_lines)
    if not notes:
        return fallback
    return _summarize(
        f"Objective: {objective}\n\nCompleted task notes:\n{notes}\n\n"
        f"Write a short final report for the developer: what was done, where (exact "
        f"file paths), and anything still open.", fallback, max_tokens=400)
