"""Typed configuration schema for providers / roles / limits.

The shape is final from Phase 1 even though only `openai-compatible` providers are
implemented — user config files written now must never break when cloud providers land.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderCfg:
    name: str
    type: str = "openai-compatible"        # openai-compatible | anthropic | openai
    base_url: str = ""                     # for openai-compatible servers
    api_key_env: str = ""                  # cloud providers read the key from this env var

    @property
    def is_remote(self) -> bool:
        """True when calls would leave this machine (privacy gate applies)."""
        if self.type != "openai-compatible":
            return True
        host = self.base_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        return host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "")


@dataclass(frozen=True)
class McpServerCfg:
    """One external MCP stdio server: [mcp_servers.<name>] in config.toml."""
    name: str
    command: str
    args: tuple = ()
    read_only: bool = False    # default MUTATE (approval-gated); opt-in to READ


@dataclass(frozen=True)
class LimitsCfg:
    max_task_steps: int = 10
    max_total_steps: int = 40
    max_tasks: int = 8
    wall_clock_minutes: int = 20


@dataclass(frozen=True)
class UpdaterCfg:
    """[updater] — read from the USER config layer only (a project's .magepilot.toml
    must never be able to flip auto-update for the whole install)."""
    auto_update: bool = True
    channel: str = "stable"     # stable = release tags only | edge = tip of main


@dataclass
class Config:
    providers: dict[str, ProviderCfg] = field(default_factory=dict)
    # role -> "provider:model" | "@alias". Roles are prompts, not necessarily models.
    roles: dict[str, str] = field(default_factory=dict)
    limits: LimitsCfg = field(default_factory=LimitsCfg)
    sampling: dict = field(default_factory=dict)
    strict_models: bool = False            # fail instead of substituting the loaded model
    mcp_servers: dict[str, McpServerCfg] = field(default_factory=dict)
    updater: UpdaterCfg = field(default_factory=UpdaterCfg)
