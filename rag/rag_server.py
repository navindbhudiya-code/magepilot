"""OpenAI-compatible RAG proxy.

Sits in front of the local model server (mlx_lm.server on :8080). For each chat request it retrieves
relevant Magento facts, injects them as a system-context message, and forwards to the model with the
correct model id + sampling. Point PhpStorm / any OpenAI client at:  http://127.0.0.1:8090/v1

    python rag/rag_server.py
"""
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from ask import SYSTEM, model_id
from retriever import retrieve


def forward(payload: dict) -> dict:
    req = urllib.request.Request(
        config.MODEL_SERVER + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, payload: dict) -> None:
        """Relay the model server's SSE stream to the client as tokens arrive."""
        req = urllib.request.Request(
            config.MODEL_SERVER + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            upstream = urllib.request.urlopen(req, timeout=300)
        except Exception as exc:  # noqa: BLE001
            self._send(502, {"error": str(exc)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            with upstream:
                for line in upstream:
                    self.wfile.write(line)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "magento-rag", "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")

        contexts = retrieve(last_user)
        if contexts:
            block = "\n\n".join(f"[{c['title']}]\n{c['text']}" for c in contexts)
            sys_msg = {"role": "system", "content": SYSTEM + "\n\n## Magento reference context\n" + block}
            messages = [sys_msg] + [m for m in messages if m.get("role") != "system"]

        want_stream = bool(body.get("stream"))
        payload = {**body, "messages": messages, "model": model_id(), "stream": want_stream}
        payload.setdefault("stop", ["<|im_end|>"])
        for key, val in config.SAMPLING.items():
            payload.setdefault(key, val)

        if want_stream:
            self._stream(payload)
            return
        try:
            self._send(200, forward(payload))
        except Exception as exc:  # noqa: BLE001
            self._send(502, {"error": str(exc)})

    def log_message(self, *args):  # silence default logging
        pass


def main() -> None:
    srv = ThreadingHTTPServer((config.PROXY_HOST, config.PROXY_PORT), Handler)
    print(f"RAG proxy: http://{config.PROXY_HOST}:{config.PROXY_PORT}/v1  ->  model {config.MODEL_SERVER}")
    print("Point PhpStorm / OpenAI clients here for grounded, house-style Magento answers.")
    srv.serve_forever()


if __name__ == "__main__":
    main()
