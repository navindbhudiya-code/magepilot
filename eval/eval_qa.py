"""Practical QA of the fine-tuned model: implementation, error-handling, and DEBUGGING tasks.

Tests the RAW model (no RAG) with production sampling, to assess the model's own capability.
Requires the model server (serving/serve.sh on :8080). Writes eval/reports/qa-practical.md.
Run:  python eval/eval_qa.py
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "rag"))
from ask import model_id  # noqa: E402

ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"
SAMPLING = {"temperature": 0.3, "top_p": 0.95, "repetition_penalty": 1.1, "max_tokens": 600}
SYSTEM = (
    "You are a senior Magento 2 engineer. Give correct, idiomatic, modern Magento 2 answers "
    "(PHP 8, service contracts, plugins over preferences, declarative schema, Hyva + Alpine + Tailwind). "
    "For debugging questions, give concrete diagnostic steps and the exact bin/magento commands."
)

TASKS = [
    ("practical", "Write a plugin that adds a fixed surcharge to a product's final price, with the di.xml."),
    ("practical", "Create a console command `vendor:catalog:export` that exports products to CSV with a progress bar."),
    ("practical", "Write a service method that loads active products for a list of SKUs in a single query (avoid N+1)."),
    ("practical", "Build a Hyva product quick-view modal with Alpine.js and Tailwind."),
    ("practical", "Add a customer-scoped GraphQL query that returns the logged-in customer's recent orders."),
    ("error",     "Write an admin save controller that validates input, persists via a repository, and handles errors with proper messages and redirects."),
    ("error",     "Implement a repository getById that throws NoSuchEntityException, and explain how that maps to a REST HTTP status."),
    ("error",     "A customer submits an invalid email to my service. Which Magento exception should I throw and why?"),
    ("error",     "How do I CSRF-protect a storefront form POST controller in Magento 2?"),
    ("debug",     "My plugin's afterGetName method is never called. How do I debug why?"),
    ("debug",     "Running my custom CLI command throws 'Area code is not set'. How do I fix it?"),
    ("debug",     "My cron job's status is 'missed' and it never runs. How do I diagnose and fix it?"),
    ("debug",     "I added a column in db_schema.xml but setup:upgrade did not create it. Why?"),
    ("debug",     "My storefront shows a blank white page (HTTP 500). How do I find the root cause?"),
    ("debug",     "I changed di.xml to add a preference but it isn't taking effect. What's wrong?"),
]
OUT = os.path.join(HERE, "reports", "qa-practical.md")


def call(prompt: str) -> tuple[str, float]:
    payload = {
        "model": model_id(),
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "stop": ["<|im_end|>"],
        **SAMPLING,
    }
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    return text.split("<|im_end|>")[0].strip(), time.time() - t0


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Practical QA — fine-tuned model (raw, no RAG)\n\n")
        f.write(f"Model: `{model_id()}` · sampling temp 0.3 / top_p 0.95 / rep_penalty 1.1 / max_tokens 600\n\n")
        for i, (cat, prompt) in enumerate(TASKS, 1):
            ans, dt = call(prompt)
            print(f"[{cat:9}] {i:2d}/{len(TASKS)} ({dt:5.1f}s) {prompt[:48]}", flush=True)
            f.write(f"\n---\n\n## {i}. [{cat.upper()}] {prompt}\n\n````\n{ans}\n````\n\n")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
