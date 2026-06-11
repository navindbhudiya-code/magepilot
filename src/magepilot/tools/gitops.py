"""Git intelligence tools (docs/architecture/08, Phase 4).

READ tier: status / diff / log / blame — the agent understands recent changes,
affected files, and author history without leaving the loop.
MUTATE tier: branch / add / commit — gated by the registry's approval gate like every
other write. There is deliberately NO push tool in v1: nothing the agent does leaves
the machine.

Safety: fixed argv (never a shell), `git -C <realpath(root)>`, validated branch names,
paths sandboxed through _safe_path, bounded timeouts, clipped output.
"""
import os
import re
import subprocess

from magepilot.safety.sandbox import _safe_path
from magepilot.tools.base import Param, RiskLevel, Tool, ToolError, clip as _clip, root_tool

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,200}$")
GIT_TIMEOUT = 30


def _git(root: str, *args: str, timeout: int = GIT_TIMEOUT) -> str:
    try:
        out = subprocess.run(["git", "-C", os.path.realpath(root), *args],
                             capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        return f"git failed: {e}"
    if out.returncode != 0:
        return f"git error: {(out.stderr or out.stdout).strip()[:400]}"
    return out.stdout


def _rel(root: str, path: str) -> str:
    """Sandbox a user/model-supplied path and return it relative to the repo root."""
    full = _safe_path(root, path)               # raises ToolError outside the root
    return os.path.relpath(full, os.path.realpath(root))


# --------------------------------------------------------------------------- READ
def git_status(root: str) -> str:
    """Branch + working-tree summary (porcelain)."""
    out = _git(root, "status", "--porcelain=v1", "-b", "--untracked-files=all")
    if out.startswith("git "):
        return out
    lines = out.splitlines()
    head = lines[0] if lines else ""
    changes = lines[1:]
    if not changes:
        return _clip(f"{head}\nworking tree clean")
    return _clip(f"{head}\n{len(changes)} changed file(s):\n" + "\n".join(changes[:60]))


def git_diff(root: str, path: str = None, staged: bool = False) -> str:
    """The uncommitted diff (optionally one path; staged=true for the index)."""
    args = ["diff", "--no-color"] + (["--cached"] if staged else [])
    if path:
        args += ["--", _rel(root, path)]
    out = _git(root, *args)
    if out.startswith("git "):
        return out
    return _clip(out) if out.strip() else "no diff" + (" (staged)" if staged else "")


def git_log(root: str, path: str = None, n: int = 10) -> str:
    """Recent commits (optionally for one path) — who changed what, when."""
    args = ["log", f"-{max(1, min(int(n or 10), 50))}",
            "--date=short", "--pretty=format:%h %ad %an  %s"]
    if path:
        args += ["--follow", "--", _rel(root, path)]
    out = _git(root, *args)
    return _clip(out) if out.strip() and not out.startswith("git ") else (out or "no commits")


def git_blame(root: str, path: str, start: int = None, end: int = None) -> str:
    """Who last touched these lines (file required; start/end optional)."""
    rel = _rel(root, path)
    args = ["blame", "--date=short"]
    if start:
        args += ["-L", f"{int(start)},{int(end or start)}"]
    args += ["--", rel]
    out = _git(root, *args)
    return _clip(out) if not out.startswith("git ") else out


# --------------------------------------------------------------------------- MUTATE
def git_branch(root: str, name: str, create: bool = True) -> str:
    """Create-and-switch (default) or switch to a branch."""
    name = (name or "").strip()
    if not _BRANCH_RE.match(name) or ".." in name:
        raise ToolError(f"invalid branch name: {name!r}")
    out = _git(root, "checkout", *(["-b"] if create else []), name)
    if out.startswith("git error") and create and "already exists" in out:
        out = _git(root, "checkout", name)
    return _clip(out.strip() or f"on branch {name}")


def git_add(root: str, path: str = ".") -> str:
    """Stage a file or directory (sandboxed)."""
    rel = _rel(root, path)
    out = _git(root, "add", "--", rel)
    if out.startswith("git "):
        return out
    return f"staged {rel}"


def git_commit(root: str, message: str) -> str:
    """Commit the staged changes with a message."""
    message = (message or "").strip()
    if not message:
        raise ToolError("commit message must not be empty")
    out = _git(root, "commit", "-m", message)
    if out.startswith("git error"):
        return out
    first = next((ln for ln in out.splitlines() if ln.strip()), "committed")
    return _clip(first)


TOOLS = (
    Tool(
        name="git_status", fn=root_tool(git_status), primary="", risk=RiskLevel.READ,
        params=(),
        description="Current branch + changed files (git status). Use to see what is "
                    "already modified before editing.",
    ),
    Tool(
        name="git_diff", fn=root_tool(git_diff), primary="path", risk=RiskLevel.READ,
        params=(Param("path", description="limit to one file"),
                Param("staged", type="boolean", description="true = the staged diff")),
        description="The uncommitted diff (optionally for one file). Use to inspect what "
                    "changed recently — e.g. 'what changed checkout yesterday' starts here.",
    ),
    Tool(
        name="git_log", fn=root_tool(git_log), primary="path", risk=RiskLevel.READ,
        params=(Param("path", description="history of one file"),
                Param("n", type="integer", description="number of commits (default 10)")),
        description="Recent commit history, optionally for one file (follows renames). "
                    "Use to find which commit introduced a change.",
    ),
    Tool(
        name="git_blame", fn=root_tool(git_blame), primary="path", risk=RiskLevel.READ,
        params=(Param("path", required=True, description="file to blame"),
                Param("start", type="integer", description="first line"),
                Param("end", type="integer", description="last line")),
        description="Who last touched specific lines of a file (git blame, optionally a "
                    "line range). Use after locating a suspicious line.",
    ),
    Tool(
        name="git_branch", fn=root_tool(git_branch), primary="name", risk=RiskLevel.MUTATE,
        params=(Param("name", required=True, description="branch name"),
                Param("create", type="boolean", description="false = switch only")),
        description="Create and switch to a branch (requires approval). Do this BEFORE "
                    "a multi-file change so the work is isolated.",
    ),
    Tool(
        name="git_add", fn=root_tool(git_add), primary="path", risk=RiskLevel.MUTATE,
        params=(Param("path", description="file or directory (default '.')"),),
        description="Stage changes for commit (requires approval).",
    ),
    Tool(
        name="git_commit", fn=root_tool(git_commit), primary="message", risk=RiskLevel.MUTATE,
        params=(Param("message", required=True, description="the commit message"),),
        description="Commit the STAGED changes (requires approval). Stage with git_add "
                    "first. There is no push tool — nothing leaves this machine.",
    ),
)
