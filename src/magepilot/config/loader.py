"""TOML config loading with layered precedence:

    built-in defaults  ←  ~/.magepilot/config.toml  ←  <project>/.magepilot.toml  ←  env

Env back-compat: MODEL_SERVER overrides the `local` provider's base_url and AGENT_MODEL
pins the executor model, exactly as in v1. The default config contains NO cloud providers —
remote models exist only when the user writes them into a config file themselves.
"""
import os
import tomllib

import magepilot.config.defaults as defaults
from magepilot.config.schema import Config, LimitsCfg, McpServerCfg, ProviderCfg

USER_CONFIG = os.path.expanduser("~/.magepilot/config.toml")
PROJECT_CONFIG_NAME = ".magepilot.toml"

DEFAULT_ROLES = {
    "executor": "local:auto",       # auto = prefer the base Instruct model, avoid the fine-tune
    "coder": "local:" + defaults.MODEL_MATCH,
    "planner": "@executor",
    "summarizer": "@executor",
    "reviewer": "@executor",
}


def _read_toml(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"warning: ignoring unreadable config {path}: {e}")
        return {}


def load(project_root: str | None = None) -> Config:
    """Build the effective Config for a session."""
    data: dict = {}
    for path in (USER_CONFIG,
                 os.path.join(project_root, PROJECT_CONFIG_NAME) if project_root else None):
        if path:
            _merge(data, _read_toml(path))

    providers = {"local": ProviderCfg(name="local", base_url=defaults.MODEL_SERVER)}
    for name, p in (data.get("providers") or {}).items():
        providers[name] = ProviderCfg(
            name=name,
            type=p.get("type", "openai-compatible"),
            base_url=p.get("base_url", ""),
            api_key_env=p.get("api_key_env", ""),
        )

    roles = dict(DEFAULT_ROLES)
    roles.update(data.get("roles") or {})

    lim = data.get("limits") or {}
    limits = LimitsCfg(
        max_task_steps=int(lim.get("max_task_steps", LimitsCfg.max_task_steps)),
        max_total_steps=int(lim.get("max_total_steps", LimitsCfg.max_total_steps)),
        max_tasks=int(lim.get("max_tasks", LimitsCfg.max_tasks)),
        wall_clock_minutes=int(lim.get("wall_clock_minutes", LimitsCfg.wall_clock_minutes)),
    )

    mcp_servers = {}
    for name, m in (data.get("mcp_servers") or {}).items():
        if m.get("command"):
            mcp_servers[name] = McpServerCfg(
                name=name, command=m["command"], args=tuple(m.get("args") or ()),
                read_only=bool(m.get("read_only", False)))

    cfg = Config(
        providers=providers,
        roles=roles,
        limits=limits,
        sampling={**defaults.SAMPLING, **(data.get("sampling") or {})},
        strict_models=bool(data.get("strict_models", False)),
        mcp_servers=mcp_servers,
    )

    # Env wins over files (v1 back-compat).
    if os.environ.get("MODEL_SERVER"):
        cfg.providers["local"] = ProviderCfg(name="local", base_url=os.environ["MODEL_SERVER"])
    if os.environ.get("AGENT_MODEL"):
        cfg.roles["executor"] = "local:" + os.environ["AGENT_MODEL"]
    return cfg


def _merge(base: dict, extra: dict) -> None:
    """Recursive dict merge — later layers override earlier ones key-by-key."""
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
