"""The tool abstraction: every agent capability is a Tool with a name, a prompt-tuned
description, a typed param schema, and a risk tier.

The wire format the 7B model speaks stays ReAct text (Action / Action Input) — proven on
this model and protected by tests — but `Tool.params` converts mechanically to JSON Schema,
which is what makes MCP exposure and native tool-calls for capable providers possible later
without touching the registry. Format is presentation; the registry is the contract.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from magepilot.errors import ToolError  # noqa: F401 — canonical home is magepilot.errors


class RiskLevel(Enum):
    READ = "auto"              # runs without asking (all v1 tools)
    MUTATE = "ask"             # writes files / state-changing commands — per-call approval
    DANGEROUS = "ask_always"   # never auto-approvable via "always"


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str                 # exact prose shown in the system prompt (prompt-tuned)
    fn: Callable[..., str]           # fn(ctx, **kwargs) -> str; raises ToolError only
    params: tuple[Param, ...] = ()
    primary: str = ""                # the param a bare-string Action Input maps to
    risk: RiskLevel = RiskLevel.READ

    def json_schema(self) -> dict:
        """The tool's input schema — the MCP / native-tool-call representation."""
        return {
            "type": "object",
            "properties": {
                p.name: {"type": p.type, "description": p.description} for p in self.params
            },
            "required": [p.name for p in self.params if p.required],
        }


@dataclass
class ToolContext:
    """What a tool may reach: the sandbox root plus session services (config, approver,
    and — in later phases — run state and memory). Replaces v1's bare `root: str`."""
    root: str
    config: Any = None
    approver: Any = None
    run_state: Any = None


def root_tool(fn: Callable[..., str]) -> Callable[..., str]:
    """Adapt a v1-style `fn(root, **kwargs)` tool function to the `fn(ctx, **kwargs)`
    Tool interface — keeps the migrated function bodies byte-identical."""
    def _adapted(ctx: ToolContext, **kwargs) -> str:
        return fn(ctx.root, **kwargs)
    _adapted.__name__ = getattr(fn, "__name__", "tool")
    _adapted.__doc__ = fn.__doc__
    return _adapted


def clip(text: str, limit: int | None = None) -> str:
    """Truncate an observation to the configured cap (v1 _clip)."""
    from magepilot import config
    limit = limit if limit is not None else config.MAX_OBS_CHARS
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} more chars]"
