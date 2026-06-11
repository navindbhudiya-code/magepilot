"""Provider implementations.

Phase 1 implements `openai-compatible` (covers mlx_lm.server, Ollama, llama.cpp, vLLM).
`anthropic` / `openai` are declared in the config schema now — so user configs never
break — but their call paths land in Phase 6.
"""
from magepilot.config.schema import ProviderCfg
from magepilot.llm import client


class ProviderError(Exception):
    pass


def chat(provider: ProviderCfg, model: str, messages: list[dict],
         stop: list[str] | None = None, sampling: dict | None = None,
         timeout: int = 300) -> str:
    if provider.type == "openai-compatible":
        return client.complete(provider.base_url, model, messages,
                               stop=stop, sampling=sampling, timeout=timeout)
    raise ProviderError(
        f"provider type '{provider.type}' is not implemented yet (lands in Phase 6) — "
        f"use an openai-compatible endpoint for now")


def loaded_models(provider: ProviderCfg) -> list[str]:
    if provider.type == "openai-compatible":
        return client.list_models(provider.base_url)
    return []
