"""The one OpenAI-compatible HTTP client (consolidates the duplicated urllib call sites
that lived in react_agent.call_model, edits.generate_plan, and repl._ask_via_proxy)."""
import json
import urllib.request


def complete(base_url: str, model: str, messages: list[dict],
             stop: list[str] | None = None, sampling: dict | None = None,
             timeout: int = 300, headers: dict | None = None) -> str:
    """Blocking chat completion. Returns the assistant message content."""
    payload = {"model": model, "messages": messages, **(sampling or {})}
    if stop:
        payload["stop"] = stop
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def stream(base_url: str, model: str, messages: list[dict],
           sampling: dict | None = None, timeout: int = 300):
    """Yield content chunks from an SSE streaming chat completion."""
    payload = {"model": model, "messages": messages, "stream": True, **(sampling or {})}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except (ValueError, KeyError, IndexError):
                continue
            chunk = delta.get("content") or ""
            if chunk:
                yield chunk


def list_models(base_url: str, timeout: int = 5) -> list[str]:
    """Model ids the server has loaded. Empty list when unreachable."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as r:
            return [m["id"] for m in json.load(r)["data"]]
    except Exception:
        return []
