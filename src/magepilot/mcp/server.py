"""The MCP server: MagePilot's tools for any MCP client, over stdio.

Register it in Claude Code with:

    claude mcp add magepilot -- /path/to/magepilot mcp-serve --root /path/to/store

stdout carries ONLY newline-delimited JSON-RPC; everything human goes to stderr.
READ tools are exposed by default. --allow-writes additionally exposes MUTATE/DANGEROUS
tools with auto-approval — the CLIENT's permission gate (e.g. Claude Code's) is then the
human gate, and the sandbox, linter, and undo journal still apply on this side.
"""
import json
import sys

from magepilot import __version__
from magepilot.tools import REGISTRY
from magepilot.tools.base import RiskLevel, ToolContext

PROTOCOL_VERSION = "2024-11-05"


def _tool_specs(allow_writes: bool) -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.json_schema()}
        for t in REGISTRY._tools.values()
        if allow_writes or t.risk is RiskLevel.READ
    ]


def handle(msg: dict, ctx: ToolContext, allow_writes: bool) -> dict | None:
    """One JSON-RPC message → response dict (None for notifications)."""
    method, msg_id = msg.get("method", ""), msg.get("id")
    if method.startswith("notifications/"):
        return None

    def result(payload) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": payload}

    if method == "initialize":
        return result({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "magepilot", "version": __version__},
        })
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": _tool_specs(allow_writes)})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        tool = REGISTRY.get(name)
        if tool is not None and tool.risk is not RiskLevel.READ and not allow_writes:
            out = f"error: tool '{name}' is write-capable — start the server with --allow-writes"
        else:
            out = REGISTRY.dispatch(ctx, name, json.dumps(params.get("arguments") or {}))
        return result({"content": [{"type": "text", "text": out}],
                       "isError": out.startswith(("error:", "unknown tool"))})
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(root: str, allow_writes: bool = False,
          stdin=None, stdout=None) -> None:
    """Blocking stdio loop. Never crashes on a bad message; EOF ends it."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    ctx = ToolContext(root=root,
                      approver=(lambda t, a: "yes") if allow_writes else None)
    print(f"magepilot MCP server: root={root}, "
          f"{'READ+WRITE' if allow_writes else 'READ-only'} "
          f"({len(_tool_specs(allow_writes))} tools)", file=sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle(msg, ctx, allow_writes)
        except Exception as e:        # a tool bug must not kill the transport
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": f"internal error: {e}"}}
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
