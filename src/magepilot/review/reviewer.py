"""Reviewer: one model call, regex-parsed ISSUE lines, garbage discarded. If the
reviewer role is mapped to a cloud model in config, this is where a big model adds the
most value per token."""
import re
import subprocess
from dataclasses import dataclass

from magepilot.llm.router import get_router
from magepilot.review.checklists import REVIEW_SYSTEM

_ISSUE_RE = re.compile(
    r"^ISSUE:\s*(\S+?):(\d+)\s*\[(arch|conv|sec|perf)\]\s*(low|medium|high)\s+(.+)$", re.I)

MAX_DIFF_CHARS = 9000      # the 7B's window; biggest hunks first would be better — later


@dataclass(frozen=True)
class Issue:
    file: str
    line: int
    category: str
    severity: str
    text: str


def parse_issues(text: str) -> list[Issue]:
    issues = []
    for line in (text or "").splitlines():
        m = _ISSUE_RE.match(line.strip())
        if m:
            issues.append(Issue(file=m.group(1), line=int(m.group(2)),
                                category=m.group(3).lower(), severity=m.group(4).lower(),
                                text=m.group(5).strip()))
    return issues


def review_diff(diff: str, complete=None) -> list[Issue] | None:
    """Issues for a diff; None when the model is unreachable. Unparseable output → []
    (advisory layer — the deterministic lints already ran and are the real gate)."""
    if not (diff or "").strip():
        return []
    if complete is None:
        def complete(messages):
            return get_router().complete("reviewer", messages, stop=["<|im_end|>"],
                                         sampling={"max_tokens": 600}, timeout=240)
    messages = [{"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": f"Diff:\n```\n{diff[:MAX_DIFF_CHARS]}\n```"}]
    try:
        out = complete(messages)
    except Exception:
        return None
    return parse_issues(out)


def uncommitted_diff(root: str) -> str:
    parts = []
    for args in (["diff"], ["diff", "--cached"]):
        try:
            out = subprocess.run(["git", "-C", root, *args], capture_output=True,
                                 text=True, timeout=30)
            if out.returncode == 0:
                parts.append(out.stdout)
        except (subprocess.SubprocessError, OSError):
            pass
    return "\n".join(p for p in parts if p.strip())


def review_uncommitted(root: str) -> list[Issue] | None:
    diff = uncommitted_diff(root)
    if not diff.strip():
        return None
    return review_diff(diff)
