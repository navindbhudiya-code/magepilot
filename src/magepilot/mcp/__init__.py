"""MCP interop (docs/architecture/08, Phase 6).

server: expose MagePilot's tool registry to any MCP client (Claude Code, IDEs) over
stdio — the registry has carried JSON Schemas since Phase 1 exactly for this; the wire
format was always presentation. READ tools only by default.

client: consume external stdio MCP servers declared in config.toml; their tools join
the same registry behind the same permission gate (MUTATE by default).

Both sides hand-roll the small JSON-RPC subset they need (newline-delimited, protocol
2024-11-05) — no SDK dependency.
"""
