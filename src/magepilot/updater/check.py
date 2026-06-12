"""Update check — local version vs the latest release. Fail-silent EVERYWHERE: the
check runs detached in the background, so any error (offline, rate limit, missing git,
no tags) means "no update", never an exception.

Channels: stable = newest vX.Y.Z release tag (GitHub API) · edge = tip of origin/main
(git ls-remote — no API, no rate limit).
"""
import json
import os
import re
import subprocess
import urllib.request

from magepilot import config

_VER_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:$|[-+.])")
HTTP_TIMEOUT = 3.0


def parse_version(s) -> tuple | None:
    """'v0.2.0' / '0.10.1' / 'v0.2.0-5-g158c5a0' → (0, 2, 0); unparseable → None."""
    m = _VER_RE.match((s or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def is_newer(remote, local) -> bool:
    r, l = parse_version(remote), parse_version(local)
    return bool(r and l and r > l)


def _git(root: str, *args: str, timeout: int = 15) -> tuple[int, str]:
    """(rc, stdout). Sanitized env: GIT_DIR/GIT_WORK_TREE from a caller's git hook must
    not redirect us to the wrong repo, and a credential prompt would hang a detached
    process forever."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        out = subprocess.run(["git", "-C", root, *args], capture_output=True,
                             text=True, timeout=timeout, env=env)
        return out.returncode, (out.stdout or "").strip()
    except (subprocess.SubprocessError, OSError):
        return 1, ""


def _http_get(url: str, timeout: float = HTTP_TIMEOUT):
    """Bytes or None. GitHub returns 403 without a User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": "magepilot-updater",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def local_version(root: str) -> str:
    rc, out = _git(root, "describe", "--tags", "--always")
    if rc == 0 and parse_version(out):
        return out
    from magepilot import __version__   # shallow clone without tags → packaged version
    return "v" + __version__


def latest_release(timeout: float = HTTP_TIMEOUT) -> tuple:
    """(tag, html_url) of the newest GitHub release, or (None, None)."""
    raw = _http_get(config.UPDATE_API_URL, timeout)
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
    except ValueError:
        return None, None
    tag = data.get("tag_name") or ""
    if not parse_version(tag):
        return None, None
    url = data.get("html_url") or f"https://github.com/{config.UPDATE_REPO_SLUG}/releases/tag/{tag}"
    return tag, url


def check(root: str, channel: str = "stable", timeout: float = HTTP_TIMEOUT) -> dict:
    """{'local', 'latest', 'url', 'update_available'} — never raises."""
    local = local_version(root)
    if channel == "edge":
        rc, out = _git(root, "ls-remote", "origin", "refs/heads/main", timeout=int(timeout) + 7)
        remote_sha = out.split()[0] if rc == 0 and out else ""
        rc, head = _git(root, "rev-parse", "HEAD")
        available = bool(remote_sha and head and remote_sha != head)
        return {"local": local, "latest": remote_sha or None,
                "url": f"https://github.com/{config.UPDATE_REPO_SLUG}/commits/main",
                "update_available": available}
    tag, url = latest_release(timeout)
    return {"local": local, "latest": tag, "url": url,
            "update_available": is_newer(tag, local)}
