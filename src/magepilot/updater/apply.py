"""Staged update apply — every rail must pass before the working tree is touched.

Rail order (minimizes the TOCTOU window; --ff-only is the final backstop):
  lock → installer-managed (background only) → on main + clean → unshallow/fetch
  → pick target (stable: newest v* tag · edge: origin/main) → ancestor check
  → servers check LAST → ff merge → conditional dep reinstall → state + notice flag.

Any rail failing → log one line, exit cleanly. The user must never feel this.
"""
import os
import shutil
import socket
import subprocess
import time

from magepilot import config
from magepilot.updater import state
from magepilot.updater.check import _git, is_newer, local_version, parse_version


# --------------------------------------------------------------------------- seams
def _servers_running() -> bool:
    """The model (:8080) / RAG (:8090) servers — port probe + the wrapper's PID files."""
    for port in (8080, 8090):
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
    rundir = os.path.join(config.REPO_ROOT, ".magepilot")
    for name in ("model.pid", "rag.pid"):
        path = os.path.join(rundir, name)
        try:
            with open(path) as f:
                pid = int(f.read().strip() or 0)
        except (OSError, ValueError):
            continue
        if pid and _pid_alive(pid):
            return True
    return False


def _pid_alive(pid: int) -> bool:
    if os.name != "posix":
        return True            # can't probe safely on Windows — assume alive (conservative)
    try:
        os.kill(pid, 0)        # signal 0 = existence check (POSIX only — kills on Windows!)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _install_deps(root: str) -> tuple[bool, str]:
    """uv pip install -e <root> into the install's venv — only called when
    pyproject.toml / uv.lock changed between old and new HEAD."""
    uv = shutil.which("uv")
    if not uv:
        return False, "uv not found on PATH"
    py = os.path.join(root, "mlx-env",
                      "Scripts" if os.name == "nt" else "bin",
                      "python.exe" if os.name == "nt" else "python")
    if not os.path.exists(py):
        return False, "venv missing (run: magepilot install)"
    try:
        out = subprocess.run([uv, "pip", "install", "-q", "-e", root, "--python", py],
                             capture_output=True, text=True, timeout=600)
        return out.returncode == 0, (out.stderr or "").strip()[:400]
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)


def _is_managed_install(root: str) -> bool:
    """Background auto-update only touches the installer-managed clone — a dev
    checkout on a clean main must never be silently pulled."""
    home = os.environ.get("MAGEPILOT_HOME") or os.path.expanduser("~/.magepilot")
    try:
        return os.path.realpath(root) == os.path.realpath(home)
    except OSError:
        return False


# --------------------------------------------------------------------------- lock
def acquire_lock() -> bool:
    path = config.UPDATE_LOCK_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return False
    if os.path.exists(path) and _lock_is_stale(path):
        try:
            os.unlink(path)
        except OSError:
            pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        os.unlink(config.UPDATE_LOCK_FILE)
    except OSError:
        pass


def _lock_is_stale(path: str) -> bool:
    """Stale = older than UPDATE_LOCK_STALE_S (portable), or its PID is gone (POSIX)."""
    try:
        if time.time() - os.path.getmtime(path) > config.UPDATE_LOCK_STALE_S:
            return True
    except OSError:
        return False
    if os.name == "posix":
        try:
            with open(path) as f:
                pid = int(f.read().strip() or 0)
        except (OSError, ValueError):
            return True
        return bool(pid) and not _pid_alive(pid)
    return False


# --------------------------------------------------------------------------- rails
def rails(root: str) -> str | None:
    """None when safe to proceed; otherwise the (logged) reason to skip."""
    if not shutil.which("git") or not os.path.isdir(os.path.join(root, ".git")):
        return "not a git checkout"
    rc, branch = _git(root, "symbolic-ref", "--short", "HEAD")
    if rc != 0 or branch != "main":
        return f"not on main (on '{branch or 'detached HEAD'}')"
    # --untracked-files=no: a real install (~/.magepilot) legitimately holds untracked
    # files at the clone root (config.toml, plus ignored mlx-env/logs/.magepilot) —
    # untracked files can't break an ff merge, and a path collision aborts the merge
    # itself, which the error path catches.
    rc, dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    if rc != 0:
        return "git status failed"
    if dirty:
        return "working tree not clean"
    return None


def _pick_stable_tag(root: str, local: str) -> str | None:
    """Newest vX.Y.Z tag newer than the local version (tags are fresh post-fetch)."""
    rc, out = _git(root, "tag", "--list", "v*", "--sort=-v:refname")
    if rc != 0:
        return None
    for tag in out.splitlines():
        if parse_version(tag) and is_newer(tag, local):
            return tag.strip()
    return None


# --------------------------------------------------------------------------- apply
def apply(root: str, *, explicit: bool = False, channel: str = "stable") -> dict:
    """Returns {'status': applied|staged|up-to-date|skipped|error, 'reason', 'old',
    'new', 'url', 'deps'}. Never raises. explicit=True (magepilot update) skips the
    managed-install guard and does not set the launch notice (output is immediate)."""
    res = {"status": "skipped", "reason": "", "old": "", "new": "", "url": "", "deps": False}
    if not explicit and not _is_managed_install(root):
        res["reason"] = "not the installer-managed install (~/.magepilot)"
        return res
    if not acquire_lock():
        res["reason"] = "another update is already running"
        return res
    try:
        return _apply_locked(root, res, explicit=explicit, channel=channel)
    except Exception as e:           # belt and braces — a detached process must exit 0
        res.update(status="error", reason=f"unexpected: {e}")
        return res
    finally:
        release_lock()


def _apply_locked(root: str, res: dict, *, explicit: bool, channel: str) -> dict:
    reason = rails(root)
    if reason:
        res["reason"] = reason
        return res
    res["old"] = local_version(root)

    # install.sh clones with --depth 1: no tags, no history for ff — deepen once.
    rc, shallow = _git(root, "rev-parse", "--is-shallow-repository")
    if shallow == "true":
        _git(root, "fetch", "--unshallow", "--tags", "origin", "main", timeout=300)
    rc, _out = _git(root, "fetch", "--tags", "origin", "main", timeout=120)
    if rc != 0:
        res.update(status="skipped", reason="git fetch failed (offline?)")
        return res

    if channel == "edge":
        target = "origin/main"
        url = f"https://github.com/{config.UPDATE_REPO_SLUG}/commits/main"
    else:
        target = _pick_stable_tag(root, res["old"])
        if not target:
            res.update(status="up-to-date", reason="no newer release tag")
            return res
        url = f"https://github.com/{config.UPDATE_REPO_SLUG}/releases/tag/{target}"
    res["url"] = url

    rc, head = _git(root, "rev-parse", "HEAD")
    rc2, target_sha = _git(root, "rev-parse", f"{target}^{{commit}}")
    if rc != 0 or rc2 != 0:
        res.update(status="error", reason="rev-parse failed")
        return res
    if head == target_sha:
        res.update(status="up-to-date", reason="already at the latest release")
        return res
    rc, _ = _git(root, "merge-base", "--is-ancestor", "HEAD", target_sha)
    if rc != 0:
        res.update(status="skipped",
                   reason="history diverged from upstream — re-run the installer to repair")
        return res

    rc, changed = _git(root, "diff", "--name-only", head, target_sha)
    changed_files = changed.splitlines() if rc == 0 else []
    touches_runtime = any(p.startswith(("src/magepilot/", "serving/")) for p in changed_files)
    new_label = target if channel != "edge" else target_sha[:12]
    # Never swap code under running servers — stage and let a later launch apply.
    if touches_runtime and _servers_running():
        state.update(staged_version=new_label)
        res.update(status="staged", new=new_label,
                   reason="model/RAG servers are running — staged for the next launch")
        return res

    rc, out = _git(root, "merge", "--ff-only", target_sha, timeout=120)
    if rc != 0:
        res.update(status="error", reason=f"ff merge failed: {out[:200]}")
        return res
    res["new"] = local_version(root)

    if any(os.path.basename(p) in ("pyproject.toml", "uv.lock") for p in changed_files):
        ok, err = _install_deps(root)
        res["deps"] = ok
        if not ok:
            # Code is updated; the wrapper's `activate` self-heal will retry the
            # install on next launch — record, don't roll back.
            res["reason"] = f"deps reinstall failed: {err}"

    state.update(staged_version="",
                 last_result={"ok": True, "old": res["old"], "new": res["new"],
                              "url": url, "error": "", "ts": time.time()},
                 notify=not explicit)
    res["status"] = "applied"
    return res
