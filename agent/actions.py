"""Approval-gated command execution.

Three tiers (the safety core):
  - AUTO    read-only commands run without asking (status/info).
  - ASK     state-changing commands require explicit approval (y / always).
  - BLOCKED everything else is refused — including shell metacharacters and any command
            not on a whitelist.

Only `php bin/magento <subcommand>` and a tiny `composer` allowlist are ever executed —
never arbitrary shell. The approval decision is a caller-provided callback, so the policy
is fully testable.
"""
import os
import re
import shutil
import subprocess

from agent import config

# read-only — run without asking (same set the agent's magento_cli tool allows)
AUTO = set(config.MAGENTO_CLI_WHITELIST)

# state-changing bin/magento commands — allowed, but only after explicit approval
ASK = {
    "setup:upgrade",
    "setup:di:compile",
    "setup:db-declaration:generate-whitelist",
    "cache:clean",
    "cache:flush",
    "indexer:reindex",
    "setup:static-content:deploy",
    "module:enable",
    "module:disable",
    "deploy:mode:set",
    "cron:run",
}

# the only composer subcommands allowed (also ASK-tier)
COMPOSER_ASK = {"dump-autoload", "dump", "install"}

_SAFE = re.compile(r"^[A-Za-z0-9:_\-./= ]+$")


def normalize(command: str) -> str:
    """Strip a leading 'php'/'bin/magento'/'magento' so we classify the bare subcommand."""
    c = (command or "").strip()
    for prefix in ("php ", "bin/magento", "magento"):
        if c.startswith(prefix):
            c = c[len(prefix):].strip()
            break
    return c


def classify(command: str) -> str:
    """Return 'auto', 'ask', or 'blocked' for a proposed command."""
    cmd = normalize(command)
    if not cmd or not _SAFE.match(cmd):
        return "blocked"
    if cmd.startswith("composer "):
        parts = cmd.split()
        return "ask" if len(parts) > 1 and parts[1] in COMPOSER_ASK else "blocked"
    sub = cmd.split(" ", 1)[0]
    if sub in AUTO:
        return "auto"
    if sub in ASK:
        return "ask"
    return "blocked"


def _argv(root: str, cmd: str):
    """Build the executable argv for an already-validated command, or (None, reason)."""
    if cmd.startswith("composer "):
        composer = shutil.which("composer")
        if not composer:
            return None, "composer not found on PATH"
        return [composer, *cmd.split()[1:]], None
    bin_magento = os.path.join(os.path.realpath(root), "bin", "magento")
    if not os.path.isfile(bin_magento):
        return None, "bin/magento not found under the codebase root"
    return ["php", bin_magento, *cmd.split(" ")], None


def execute(root: str, command: str, approver=None, allow_always: set = None) -> dict:
    """Classify, (maybe) get approval, then run the command in `root`.

    `approver(cmd, sub) -> "yes" | "no" | "always"` is called only for ASK-tier commands.
    `allow_always` is a session set of pre-approved exact commands (mutated on "always").

    Returns {ran, tier, command, [exit, output] | reason}.
    """
    cmd = normalize(command)
    tier = classify(cmd)
    if tier == "blocked":
        return {"ran": False, "tier": "blocked", "command": cmd,
                "reason": "refused: not an allowed command (or contains illegal characters)"}

    if tier == "ask":
        approved = bool(allow_always) and cmd in allow_always
        if not approved and approver is not None:
            decision = approver(cmd, cmd.split(" ", 1)[0])
            if decision == "always":
                if allow_always is not None:
                    allow_always.add(cmd)
                approved = True
            elif decision == "yes":
                approved = True
        if not approved:
            return {"ran": False, "tier": "ask", "command": cmd, "reason": "declined by user"}

    argv, err = _argv(root, cmd)
    if err:
        return {"ran": False, "tier": tier, "command": cmd, "reason": err}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=900, cwd=root)
    except (subprocess.SubprocessError, OSError) as e:
        return {"ran": False, "tier": tier, "command": cmd, "reason": f"execution error: {e}"}
    return {"ran": True, "tier": tier, "command": cmd,
            "exit": proc.returncode, "output": (proc.stdout + proc.stderr).strip()}
