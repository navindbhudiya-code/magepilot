"""The MCP client: spawn stdio servers from config.toml and register their tools.

    [mcp_servers.context7]
    command = "npx"
    args = ["-y", "@upstash/context7-mcp"]
    # read_only = true        # opt-in: skip the approval gate for this server's tools

Remote tools default to MUTATE — they're someone else's code doing who-knows-what, so
they go through the same approval gate as a file write. Names are prefixed with the
server name on collision with built-ins.
"""
import atexit
import json
import subprocess
import sys
import threading

from magepilot.config.schema import Config, McpServerCfg
from magepilot.mcp.server import PROTOCOL_VERSION
from magepilot.tools.base import Param, RiskLevel, Tool, clip as _clip

REQUEST_TIMEOUT = 30


class McpError(Exception):
    pass


class McpServer:
    """One spawned stdio MCP server (newline-delimited JSON-RPC)."""

    def __init__(self, cfg: McpServerCfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._id = 0
        try:
            self.proc = subprocess.Popen(
                [cfg.command, *cfg.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as e:
            raise McpError(f"mcp server '{cfg.name}': cannot start {cfg.command}: {e}")
        atexit.register(self.close)

    def _send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None):
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        **({"params": params} if params else {})})
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise McpError(f"mcp server '{self.cfg.name}' closed the pipe "
                                   f"(exit {self.proc.poll()})")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") != rid:
                    continue                      # a notification or stale message
                if "error" in msg:
                    raise McpError(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result")

    def initialize(self) -> None:
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "magepilot", "version": "0.2.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        return (self.request("tools/list") or {}).get("tools", [])

    def call(self, name: str, arguments: dict) -> str:
        res = self.request("tools/call", {"name": name, "arguments": arguments}) or {}
        text = "\n".join(c.get("text", "") for c in res.get("content", [])
                         if c.get("type") == "text")
        if res.get("isError"):
            return text if text.startswith("error") else f"error: {text or 'tool failed'}"
        return _clip(text or "(empty result)")

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=3)
        except Exception:
            pass


def wrap_tool(server: McpServer, spec: dict, taken: set) -> Tool:
    """An MCP tool spec → a registry Tool dispatching back through the server."""
    base = spec.get("name", "tool")
    name = base if base not in taken else f"{server.cfg.name}_{base}"
    schema = spec.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    params = tuple(Param(name=p, type=(v or {}).get("type", "string"),
                         required=p in required,
                         description=(v or {}).get("description", ""))
                   for p, v in props.items())
    primary = (params[0].name if len(params) >= 1 else "")

    def fn(ctx, **kwargs):
        return server.call(base, kwargs)

    return Tool(
        name=name,
        description=f"[{server.cfg.name}] " + (spec.get("description") or base)[:400],
        fn=fn, params=params, primary=primary,
        risk=RiskLevel.READ if server.cfg.read_only else RiskLevel.MUTATE,
    )


def attach(registry, cfg: Config, verbose: bool = True) -> list[McpServer]:
    """Spawn every configured server and register its tools. A failing server is
    reported and skipped — external processes must never break the agent."""
    servers = []
    for scfg in cfg.mcp_servers.values():
        try:
            srv = McpServer(scfg)
            srv.initialize()
            taken = set(registry.names())
            n = 0
            for spec in srv.list_tools():
                tool = wrap_tool(srv, spec, taken)
                if registry.get(tool.name) is None:
                    registry.register(tool)
                    taken.add(tool.name)
                    n += 1
            servers.append(srv)
            if verbose:
                print(f"mcp: attached '{scfg.name}' ({n} tool(s), "
                      f"{'READ' if scfg.read_only else 'approval-gated'})",
                      file=sys.stderr)
        except McpError as e:
            print(f"mcp: skipping '{scfg.name}': {e}", file=sys.stderr)
    return servers
