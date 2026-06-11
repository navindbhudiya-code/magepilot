"""The tool layer: framework (base/registry/parsing) + the sandboxed tools.

Importing this package registers the built-in tools into the default REGISTRY and
re-exports the v1 module API (read_file, grep, run_tool, tool_catalog, …) so existing
callers and tests keep working unchanged.
"""
from magepilot.safety.sandbox import _resolve_file, _safe_path          # noqa: F401 (v1 re-export)
from magepilot.graph import tool as _graphtool
from magepilot.tools import (
    debugtools as _debug, fs as _fs, gitops as _git, kb as _kb, magento as _magento,
    memorytools as _memory, sql as _sql, write as _write,
)
from magepilot.tools.base import (                                       # noqa: F401
    Param, RiskLevel, Tool, ToolContext, ToolError, clip as _clip, root_tool,
)
from magepilot.tools.fs import find_files, grep, list_dir, read_file, search_code  # noqa: F401
from magepilot.tools.kb import kb_search                                 # noqa: F401
from magepilot.tools.magento import magento_cli                          # noqa: F401
from magepilot.tools.parsing import _first_arg, _parse_args              # noqa: F401
from magepilot.tools.registry import REGISTRY, ToolRegistry              # noqa: F401
from magepilot.tools.sql import sql_query                                # noqa: F401

for _mod in (_fs, _kb, _magento, _sql, _write, _graphtool, _memory, _git, _debug):
    REGISTRY.register_many(_mod.TOOLS)


def tool_catalog() -> str:
    """Human-readable tool list for the system prompt."""
    return REGISTRY.catalog()


def run_tool(root: str, name: str, arg: str) -> str:
    """v1-compatible dispatch: build a default ToolContext for `root` and dispatch."""
    return REGISTRY.dispatch(ToolContext(root=root), name, arg)
