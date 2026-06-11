"""The tool registry: registration, catalog rendering for the system prompt, and the
single dispatch path every tool call goes through — including the permission gate.

Dispatch keeps v1's never-crash contract: a tool returns an observation string or its
error is converted into one; nothing a tool does can crash the agent loop.
"""
import sys

from magepilot.tools.base import RiskLevel, Tool, ToolContext, ToolError
from magepilot.tools.parsing import _parse_args


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def register_many(self, tools) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def catalog(self, names: tuple | None = None, include_mutating: bool = False) -> str:
        """Human-readable tool list for the system prompt (v1 tool_catalog format).

        READ-only by default: investigate/verify tasks must not be offered writes.
        `names` is a mode's visible-tool subset — with ~20 READ tools registered,
        focus is what keeps a 7B routing well. Unknown names are ignored.
        """
        return "\n".join(
            f"- {t.name}: {t.description}" for t in self._tools.values()
            if (include_mutating or t.risk is RiskLevel.READ)
            and (names is None or t.name in names))

    def dispatch(self, ctx: ToolContext, name: str, arg: str) -> str:
        """Run one tool call. `arg` is the raw Action Input (JSON object or plain string)."""
        name = (name or "").strip()
        tool = self._tools.get(name)
        if tool is None:
            return f"unknown tool '{name}'. Available: {', '.join(self._tools)}"
        if tool.risk is not RiskLevel.READ and not self._approved(ctx, tool, arg):
            return f"error: {name} requires approval and none was given"
        kwargs = _parse_args(arg, tool.primary)
        try:
            return tool.fn(ctx, **kwargs)
        except ToolError as e:
            return f"error: {e}"
        except TypeError as e:
            return f"error: bad arguments for {name} ({e})"
        except Exception as e:  # never let a tool crash the loop
            return f"error: {name} failed: {e}"

    @staticmethod
    def _approved(ctx: ToolContext, tool: Tool, arg: str) -> bool:
        """Permission gate for MUTATE/DANGEROUS tools. No approver → deny (safe default
        for non-interactive runs). DANGEROUS never accepts a remembered 'always'."""
        if ctx.approver is None:
            print(f"refused {tool.name}: no approver in a non-interactive context",
                  file=sys.stderr)
            return False
        decision = ctx.approver(tool, arg)
        if decision == "always" and tool.risk is RiskLevel.DANGEROUS:
            decision = "yes"
        return decision in ("yes", "always")


# The default process-wide registry. Tool modules register into it at import time;
# `magepilot.tools` (the package) performs those imports.
REGISTRY = ToolRegistry()
