"""Central configuration for MagePilot.

Two layers:
  - Module-level constants (from .defaults) — the knobs every subsystem reads at call
    time as `config.X`; tests may rebind them on this package.
  - load() — the typed TOML config (providers / roles / limits) consumed by the llm
    router: defaults ← ~/.magepilot/config.toml ← <project>/.magepilot.toml ← env.
"""
from magepilot.config.defaults import *          # noqa: F401,F403 — the mutable knobs
from magepilot.config.loader import load          # noqa: F401
from magepilot.config.schema import Config, LimitsCfg, ProviderCfg  # noqa: F401
