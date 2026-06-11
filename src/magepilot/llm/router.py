"""Role → (provider, model) routing with a privacy gate and a per-provider lock.

Roles are prompts, not necessarily models: in the common all-local case every role
resolves to the same loaded model and the router adds zero overhead.

Resolution of a role value:
    "provider:model"  explicit — the only way to reach a REMOTE provider
    "@alias"          follow another role's mapping (cycles refused)
    fallback          unmapped roles use the executor mapping

Model id resolution against an openai-compatible server (v1 _model_id semantics):
    "auto"            prefer the base Instruct model, avoid the style fine-tune
    "<substring>"     first loaded id containing it; if none is loaded, warn and
                      substitute the auto pick (strict_models=true errors instead)
"""
import sys
import threading

from magepilot import config as _config
from magepilot.config.schema import Config, ProviderCfg
from magepilot.llm import providers as _providers

FALLBACK_MODEL = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"


class RouterError(Exception):
    pass


class Router:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._locks: dict[str, threading.Lock] = {}
        self._announced_remote: set[str] = set()
        self._warned_substitution: set[str] = set()

    # ------------------------------------------------------------------ resolution
    def resolve(self, role: str) -> tuple[ProviderCfg, str]:
        """Resolve a role to (provider, model-spec). Raises RouterError on bad config."""
        value, seen = self.cfg.roles.get(role, "@executor"), set()
        while value.startswith("@"):
            alias = value[1:]
            if alias in seen:
                raise RouterError(f"role alias cycle: {role} -> @{alias}")
            seen.add(alias)
            value = self.cfg.roles.get(alias, "local:auto")
        provider_name, _, model = value.partition(":")
        provider = self.cfg.providers.get(provider_name)
        if provider is None:
            raise RouterError(f"role '{role}' names unknown provider '{provider_name}' — "
                              f"define [providers.{provider_name}] in ~/.magepilot/config.toml")
        if provider.is_remote:
            # The privacy gate: a remote provider is reachable ONLY because the user wrote
            # it into a role mapping — never via auto-pick or fallback. Announce once.
            if provider.name not in self._announced_remote:
                self._announced_remote.add(provider.name)
                print(f"→ sending context to {provider.name} (role={role}) — "
                      f"configured in ~/.magepilot/config.toml", file=sys.stderr)
        return provider, (model or "auto")

    def _model_id(self, provider: ProviderCfg, want: str) -> str:
        """Pin the model id against what the server actually has loaded."""
        if provider.type != "openai-compatible":
            return want
        ids = _providers.loaded_models(provider)
        if not ids:
            return want if want != "auto" else (_config.AGENT_MODEL or FALLBACK_MODEL)
        if want != "auto":
            hit = next((m for m in ids if want in m), None)
            if hit:
                return hit
            if self.cfg.strict_models:
                raise RouterError(f"model '{want}' is not loaded on {provider.base_url} "
                                  f"(loaded: {', '.join(ids)})")
            if want not in self._warned_substitution:
                self._warned_substitution.add(want)
                print(f"warning: model '{want}' not loaded on {provider.base_url} — "
                      f"substituting the loaded model", file=sys.stderr)
        # auto: prefer the base Instruct model; avoid the style fine-tune for tool reasoning
        base = next((m for m in ids if "Instruct" in m and "magento" not in m.lower()), None)
        return base or ids[0]

    # ------------------------------------------------------------------ calls
    def complete(self, role: str, messages: list[dict], stop: list[str] | None = None,
                 sampling: dict | None = None, timeout: int = 300) -> str:
        provider, want = self.resolve(role)
        model = self._model_id(provider, want)
        # Serialize calls per provider: the local server runs ONE model on shared
        # GPU/RAM — parallelism belongs to tool I/O, not LLM calls.
        lock = self._locks.setdefault(provider.name, threading.Lock())
        with lock:
            return _providers.chat(provider, model, messages, stop=stop,
                                   sampling={**self.cfg.sampling, **(sampling or {})},
                                   timeout=timeout)


_router: Router | None = None


def get_router(project_root: str | None = None) -> Router:
    """The process-wide router (config loaded once per process)."""
    global _router
    if _router is None:
        _router = Router(_config.load(project_root))
    return _router
