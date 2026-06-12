"""Updater state — one small JSON file next to the install's PID files.

Both the foreground hook and the detached background updater write here, always via
whole-file atomic replace. Every read tolerates a missing or corrupt file: the updater
must never be the reason a launch fails.
"""
import json
import os
import tempfile

from magepilot import config


def read() -> dict:
    try:
        with open(config.UPDATE_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write(data: dict) -> None:
    path = config.UPDATE_STATE_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".update_state.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, UnboundLocalError):
            pass


def update(**fields) -> dict:
    """Read-merge-replace. The only writers are the hook (last_check_ts, notify) and
    the lock-holding background updater (staged_version, last_result, notify), so a
    lost merge is at worst one redundant re-check — acceptable for a lockless merge."""
    data = read()
    data.update(fields)
    write(data)
    return data
