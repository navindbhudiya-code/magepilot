"""Auto-update — Claude-Code style: every launch silently checks in a DETACHED
background process; the update applies out-of-band; the NEXT launch prints one line.

launch_hook() runs before argument dispatch on every `python -m magepilot` invocation
with a hard budget of <50ms: it reads one small JSON file, maybe prints one stderr
line, maybe spawns one process. No git, no network, ever, in the foreground.
"""
import os
import subprocess
import sys
import time

from magepilot import config
from magepilot.updater import state
from magepilot.updater.apply import _is_managed_install   # shared dev-checkout guard


def _emit(msg: str) -> None:
    """stderr + TTY only — `magepilot sql … | jq` must never see updater output."""
    try:
        if sys.stderr.isatty():
            print(msg, file=sys.stderr)
    except (OSError, ValueError):
        pass


def _auto_update_enabled() -> bool:
    if os.environ.get("MAGEPILOT_NO_AUTO_UPDATE") == "1":
        return False
    try:
        return bool(config.load().updater.auto_update)
    except Exception:
        return True


def _spawn(cmd: list, log_path: str) -> None:
    """Fully detached: survives the parent, owns no TTY, logs to update.log."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log = open(log_path, "ab")
    except OSError:
        return
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
                    "cwd": config.REPO_ROOT}
    if os.name == "nt":
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except OSError:
        pass
    finally:
        log.close()


def _should_check(st: dict, now: float) -> bool:
    if st.get("staged_version"):       # a staged update retries on every launch
        return True
    last = st.get("last_check_ts")
    if not isinstance(last, (int, float)):
        return True
    if last > now:                     # clock skew — a future stamp must not block forever
        return True
    return (now - last) > config.UPDATE_CHECK_INTERVAL_S


def launch_hook(argv=None) -> None:
    """Called by __main__ before dispatch. Must never raise, never block."""
    try:
        argv = list(sys.argv[1:]) if argv is None else list(argv)
        if argv and argv[0] == "mcp-serve":     # stdio JSON-RPC — total silence
            return
        if os.environ.get("MAGEPILOT_NO_AUTO_UPDATE") == "1":
            return
        st = state.read()
        result = st.get("last_result") or {}
        if st.get("notify") and result.get("ok"):
            _emit(f"✦ Magepilot updated to {result.get('new', '?')} "
                  f"(changelog: {result.get('url', '')})")
            state.update(notify=False)
        if argv and argv[0] == "update":        # explicit update — don't race it
            return
        if not _is_managed_install(config.REPO_ROOT):
            return
        if not _auto_update_enabled():
            return
        now = time.time()
        if not _should_check(st, now):
            return
        state.update(last_check_ts=now)         # claim the throttle BEFORE spawning
        _spawn([sys.executable, "-m", "magepilot.updater"], config.UPDATE_LOG_FILE)
    except Exception:
        pass    # the updater must never be the reason a launch fails


def run_update(check_only: bool = False) -> int:
    """`magepilot update [--check]` — same machinery, foreground, full output.
    Works regardless of auto_update, from any clone (rails still apply)."""
    from magepilot.updater import apply as upd_apply, check as upd_check
    root = config.REPO_ROOT
    try:
        channel = config.load().updater.channel
    except Exception:
        channel = "stable"
    res = upd_check.check(root, channel=channel, timeout=10.0)
    print(f"current: {res['local']}")
    print(f"latest:  {res['latest'] or 'unknown (offline, rate-limited, or no releases)'}"
          f"   [channel: {channel}]")
    staged = state.read().get("staged_version")
    if check_only:
        if res["update_available"]:
            print("update available — run: magepilot update")
        elif staged:
            print(f"staged: {staged} — run `magepilot stop && magepilot update` to apply")
        else:
            print("✓ up to date")
        return 0
    if not res["update_available"] and not staged:
        print("✓ up to date")
        return 0
    out = upd_apply.apply(root, explicit=True, channel=channel)
    if out["status"] == "applied":
        print(f"✓ updated {out['old']} → {out['new']}"
              + ("  (dependencies reinstalled)" if out["deps"] else ""))
        if out["reason"]:
            print(f"! {out['reason']}")
        return 0
    if out["status"] == "staged":
        print(f"! {out['reason']}")
        print(f"  staged {out['new']} — apply now with: magepilot stop && magepilot update")
        return 0
    if out["status"] == "up-to-date":
        print("✓ up to date")
        return 0
    print(f"✗ not applied: {out['reason']}")
    return 1
