"""Detached background updater — `python -m magepilot.updater`.

Spawned by launch_hook; stdout/stderr are wired to logs/update.log by the parent.
ALWAYS exits 0: the foreground command has long moved on, nobody is watching."""
import os
import sys
import time

from magepilot import config
from magepilot.updater import apply as upd_apply, check as upd_check, state


def main() -> int:
    print(f"--- update check {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"(pid {os.getpid()}) ---", flush=True)
    if os.environ.get("MAGEPILOT_NO_AUTO_UPDATE") == "1":
        print("disabled by MAGEPILOT_NO_AUTO_UPDATE")
        return 0
    try:
        cfg = config.load()
        if not cfg.updater.auto_update:
            print("disabled by config ([updater] auto_update = false)")
            return 0
        channel = cfg.updater.channel
    except Exception as e:
        print(f"config load failed ({e}) — using defaults")
        channel = "stable"
    state.update(last_check_ts=time.time())
    res = upd_check.check(config.REPO_ROOT, channel=channel)
    print(f"local={res['local']} latest={res['latest']} "
          f"available={res['update_available']} channel={channel}")
    if not res["update_available"] and not state.read().get("staged_version"):
        return 0
    out = upd_apply.apply(config.REPO_ROOT, channel=channel)
    print(f"apply: {out['status']}"
          + (f" ({out['reason']})" if out.get("reason") else "")
          + (f" {out.get('old')} → {out.get('new')}" if out.get("new") else ""),
          flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:    # never leave a traceback as our only trace — log + exit 0
        print(f"updater crashed: {e}", flush=True)
        raise SystemExit(0)
