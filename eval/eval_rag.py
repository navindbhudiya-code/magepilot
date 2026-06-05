"""RAG A/B evaluation: run factual Magento questions WITHOUT vs WITH retrieval, write a report.

Demonstrates the fine-tune's factual gaps and that RAG corrects them. Requires:
  - the model server running (serving/serve.sh on :8080)
  - the vector store built (python rag/ingest.py)
Run:  python eval/eval_rag.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rag"))

from ask import build_messages, call_model  # noqa: E402
from retriever import retrieve  # noqa: E402

QUESTIONS = [
    "When should I use a plugin versus a preference in Magento 2?",
    "What is a Magento 2 preference and what is it used for?",
    "Explain when to use a plugin, a preference, and an observer.",
    "What is the difference between system.xml and config.xml?",
    "How do I add a foreign key column to an existing table with declarative schema?",
]
OUT = os.path.join(HERE, "reports", "rag-ab.md")


def run() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# RAG A/B — factual grounding (no-RAG vs RAG)\n\n")
        f.write("Same fine-tuned model; the only difference is whether retrieved Magento facts are injected.\n\n")
        for q in QUESTIONS:
            ctx = retrieve(q)
            print(f"... {q[:50]}  (retrieved {len(ctx)})", flush=True)
            no_rag = call_model(build_messages(q, []))
            with_rag = call_model(build_messages(q, ctx))
            f.write(f"\n---\n\n## {q}\n\n")
            f.write("**Retrieved:** " + (", ".join(f"`{c['source']}::{c['title']}`" for c in ctx) or "—") + "\n\n")
            f.write("### Without RAG (model alone)\n\n````\n" + no_rag.strip() + "\n````\n\n")
            f.write("### With RAG (grounded)\n\n````\n" + with_rag.strip() + "\n````\n\n")
    print("WROTE", OUT)


if __name__ == "__main__":
    run()
