"""Read-only bin/magento tool — whitelist enforced (migrated verbatim from agent/tools.py)."""
import os
import re
import subprocess

from magepilot import config
from magepilot.safety.sandbox import _safe_path
from magepilot.tools.base import Param, RiskLevel, Tool, ToolError, clip as _clip, root_tool

_CLI_SAFE = re.compile(r"^[A-Za-z0-9:_\-./= ]+$")


def magento_cli(root: str, command: str) -> str:
    """Run a READ-ONLY bin/magento command from the whitelist (e.g. 'module:status')."""
    command = (command or "").strip().removeprefix("bin/magento").removeprefix("magento").strip()
    if not _CLI_SAFE.match(command):
        raise ToolError("command rejected: illegal characters (shell metacharacters not allowed)")
    sub = command.split(" ", 1)[0]
    if sub not in config.MAGENTO_CLI_WHITELIST:
        raise ToolError(
            f"command '{sub}' is not allowed. Read-only whitelist: "
            + ", ".join(config.MAGENTO_CLI_WHITELIST)
        )
    bin_magento = _safe_path(root, "bin/magento")
    if not os.path.isfile(bin_magento):
        return "no Magento root configured (bin/magento not found under the codebase root)"
    try:
        out = subprocess.run(
            ["php", bin_magento, *command.split(" ")],
            capture_output=True, text=True, timeout=120, cwd=root,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return f"bin/magento failed: {e}"
    return _clip((out.stdout + out.stderr).strip() or "(no output)")


TOOLS = (
    Tool(
        name="magento_cli", fn=root_tool(magento_cli), primary="command", risk=RiskLevel.READ,
        params=(Param("command", required=True, description="a whitelisted bin/magento subcommand"),),
        description="Run a READ-ONLY bin/magento command from a safe whitelist "
                    "(module:status, dev:di:info, cache:status, indexer:status, setup:db:status, config:show).",
    ),
)
