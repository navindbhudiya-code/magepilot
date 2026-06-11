"""Provider implementations (Phase 6 completes the set).

- `openai-compatible`: mlx_lm.server, Ollama, llama.cpp, vLLM — and any endpoint that
  needs a bearer key via `api_key_env`.
- `openai`: openai-compatible with the official base_url default + required key +
  sampling keys the API actually accepts.
- `anthropic`: the /v1/messages shape (system extracted, stop_sequences, max_tokens
  required).

The router's privacy gate is what makes any of this safe: a remote provider is only
reachable when the user explicitly mapped a role to it in config, and the first remote
call announces itself. Payload builders are pure functions so they're testable without
a network.
"""
import json
import os
import urllib.request

from magepilot.config.schema import ProviderCfg
from magepilot.llm import client


class ProviderError(Exception):
    pass


_DEFAULT_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
# what each cloud API accepts — the local default sampling carries repetition_penalty,
# which both clouds reject
_OPENAI_SAMPLING = ("temperature", "top_p", "max_tokens")
_ANTHROPIC_SAMPLING = ("temperature", "top_p")


def _api_key(provider: ProviderCfg, required: bool) -> str:
    env = provider.api_key_env or _DEFAULT_KEY_ENV.get(provider.type, "")
    key = os.environ.get(env, "") if env else ""
    if required and not key:
        raise ProviderError(
            f"provider '{provider.name}' needs an API key — set {env or 'api_key_env'} ")
    return key


def anthropic_request(provider: ProviderCfg, model: str, messages: list[dict],
                      stop: list[str] | None, sampling: dict | None,
                      api_key: str) -> tuple[str, dict, dict]:
    """(url, headers, body) for the /v1/messages API — pure, testable."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    body = {
        "model": model,
        "messages": [m for m in messages if m["role"] != "system"],
        "max_tokens": int((sampling or {}).get("max_tokens", 1024)),
    }
    if system:
        body["system"] = system
    for k in _ANTHROPIC_SAMPLING:
        if sampling and k in sampling:
            body[k] = sampling[k]
    stops = [s for s in (stop or []) if s]
    if stops:
        body["stop_sequences"] = stops
    url = (provider.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    headers = {"Content-Type": "application/json", "x-api-key": api_key,
               "anthropic-version": "2023-06-01"}
    return url, headers, body


def parse_anthropic(data: dict) -> str:
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


def _filtered(sampling: dict | None, allowed: tuple) -> dict:
    return {k: v for k, v in (sampling or {}).items() if k in allowed}


def chat(provider: ProviderCfg, model: str, messages: list[dict],
         stop: list[str] | None = None, sampling: dict | None = None,
         timeout: int = 300) -> str:
    if provider.type == "openai-compatible":
        key = _api_key(provider, required=False)
        return client.complete(provider.base_url, model, messages, stop=stop,
                               sampling=sampling, timeout=timeout,
                               headers={"Authorization": f"Bearer {key}"} if key else None)
    if provider.type == "openai":
        key = _api_key(provider, required=True)
        return client.complete(provider.base_url or "https://api.openai.com/v1",
                               model, messages, stop=[s for s in (stop or []) if s] or None,
                               sampling=_filtered(sampling, _OPENAI_SAMPLING),
                               timeout=timeout,
                               headers={"Authorization": f"Bearer {key}"})
    if provider.type == "anthropic":
        url, headers, body = anthropic_request(provider, model, messages, stop, sampling,
                                               _api_key(provider, required=True))
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_anthropic(json.load(r))
    raise ProviderError(f"unknown provider type '{provider.type}'")


def loaded_models(provider: ProviderCfg) -> list[str]:
    if provider.type == "openai-compatible":
        return client.list_models(provider.base_url)
    return []           # cloud providers: the configured model id is taken as-is
