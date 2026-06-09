"""CLI: ask the Magento assistant a question, grounded in the knowledge base.

    python rag/ask.py "When should I use a plugin vs a preference?"
    python rag/ask.py --no-rag "..."     # ungrounded (model alone) — for A/B comparison

RAG (retrieve facts) + the fine-tuned model (house style) = grounded, idiomatic answer.
"""
import argparse
import itertools
import json
import sys
import threading
import time
import urllib.error
import urllib.request

import config
from retriever import retrieve


class _Spinner:
    """Terminal spinner while the model thinks (no-op when stderr isn't a TTY)."""
    def __init__(self, msg="thinking"):
        self.msg, self._stop, self._t = msg, threading.Event(), None

    def __enter__(self):
        if sys.stderr.isatty():
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        return self

    def _run(self):
        for c in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if self._stop.is_set():
                break
            sys.stderr.write(f"\r{c} {self.msg}… ")
            sys.stderr.flush()
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * (len(self.msg) + 6) + "\r")
        sys.stderr.flush()

    def __exit__(self, *a):
        self._stop.set()
        if self._t:
            self._t.join(timeout=0.3)

SYSTEM = (
    "You are a senior Magento 2 engineer. When the Magento reference context below is relevant, answer "
    "using it as the source of truth. If the context does not cover the question, say so briefly and "
    "answer from general knowledge. Prefer idiomatic, modern Magento 2 (PHP 8, service contracts, "
    "plugins over preferences, declarative schema, Hyva/Alpine + Tailwind). Be concise and correct."
)


def model_id() -> str:
    """Resolve the fine-tune's id from the server (it identifies by full path)."""
    try:
        with urllib.request.urlopen(config.MODEL_SERVER + "/models", timeout=5) as r:
            data = json.load(r)["data"]
        return next((m["id"] for m in data if config.MODEL_MATCH in m["id"]), data[0]["id"])
    except Exception:
        return config.MODEL_MATCH


def build_messages(question: str, contexts: list[dict]) -> list[dict]:
    if contexts:
        ctx = "\n\n".join(f"[{c['title']}]\n{c['text']}" for c in contexts)
        user = f"## Magento reference context\n{ctx}\n\n## Question\n{question}"
    else:
        user = question
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def call_model(messages: list[dict]) -> str:
    payload = {"model": model_id(), "messages": messages, "stop": ["<|im_end|>"], **config.SAMPLING}
    req = urllib.request.Request(
        config.MODEL_SERVER + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    return text.split("<|im_end|>")[0].strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Grounded Magento assistant")
    ap.add_argument("question")
    ap.add_argument("--no-rag", action="store_true", help="skip retrieval (model alone)")
    ap.add_argument("-k", type=int, default=config.TOP_K, help="number of chunks to retrieve")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="print only the answer (hide the retrieved-context listing)")
    args = ap.parse_args()

    contexts = [] if args.no_rag else retrieve(args.question, args.k)
    if contexts and not args.quiet:
        print("Retrieved context:")
        for c in contexts:
            print(f"  - {c['source']} :: {c['title']}  (dist {c['distance']:.3f})")
        print("-" * 70)
    elif not args.no_rag and not args.quiet:
        print("(no knowledge retrieved — answering from the model alone)\n")

    try:
        with _Spinner("thinking"):
            answer = call_model(build_messages(args.question, contexts))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        sys.exit(f"✗ can't reach the model server at {config.MODEL_SERVER} — "
                 f"run `magepilot serve` first.\n   ({exc})")
    print(answer)


if __name__ == "__main__":
    main()
