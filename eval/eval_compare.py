#!/usr/bin/env python3
"""STEP 4: base vs fine-tuned comparison.
Loads each model ONCE (thermal-friendly on a fanless Mac) and generates the 12
generic Magento prompts with max_tokens=400, mirroring:
  base:      mlx_lm.generate --model <BASE> --prompt "<P>" --max-tokens 400
  finetuned: mlx_lm.generate --model <BASE> --adapter-path /tmp/best-adapter --prompt "<P>" --max-tokens 400
Writes ./eval/compare.md (prompt -> BASE answer -> FINE-TUNED answer).
"""
import os, time, gc
import mlx.core as mx
from mlx_lm import load, generate

BASE = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
ADAPTER = "/tmp/best-adapter"
MAXTOK = 400
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "compare.md")

PROMPTS = [
    "Create the registration.php and module.xml for a module Vendor_Catalog.",
    "Write a plugin that modifies a product repository method, with the di.xml.",
    "When should I use a plugin versus a preference?",
    "Create an observer for catalog_product_save_after.",
    "Add a custom CLI command to Magento 2.",
    "Set up a cron job that runs hourly.",
    "Write a frontend controller for route vendor/index/view using the correct base.",
    "Create a ViewModel and use it in a Hyva .phtml template.",
    "Build a Hyva accordion component with Alpine.js and Tailwind.",
    "Add a GraphQL query with schema.graphqls and a resolver.",
    "Define a custom table with declarative schema (db_schema.xml).",
    "Load products with the repository and SearchCriteriaBuilder.",
]


def clear_cache():
    for fn in (getattr(mx, "clear_cache", None),
               getattr(getattr(mx, "metal", None), "clear_cache", None)):
        try:
            if fn:
                fn()
        except Exception:
            pass


def gen_all(model, tok, label):
    outs = []
    for i, p in enumerate(PROMPTS, 1):
        t0 = time.time()
        ids = tok.apply_chat_template([{"role": "user", "content": p}],
                                      add_generation_prompt=True)
        text = generate(model, tok, ids, max_tokens=MAXTOK, verbose=False)
        dt = time.time() - t0
        print(f"[{label}] {i:2d}/12 ({dt:5.1f}s) {p[:50]}", flush=True)
        outs.append(text)
    return outs


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    print("Loading BASE model...", flush=True)
    model, tok = load(BASE)
    base = gen_all(model, tok, "base")
    del model
    gc.collect(); clear_cache()

    print("Loading FINE-TUNED model (base + iter-160 adapter)...", flush=True)
    model, tok = load(BASE, adapter_path=ADAPTER)
    ft = gen_all(model, tok, "ft")
    del model
    gc.collect(); clear_cache()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Base vs Fine-tuned comparison\n\n")
        f.write(f"- Base model: `{BASE}`\n")
        f.write(f"- Fine-tuned: base + LoRA adapter `/tmp/best-adapter` (iter-160, val loss 1.472)\n")
        f.write(f"- max_tokens: {MAXTOK}, greedy decoding\n\n")
        for i, p in enumerate(PROMPTS):
            f.write(f"\n---\n\n## Prompt {i+1}\n\n> {p}\n\n")
            f.write("### BASE\n\n````\n" + base[i].strip() + "\n````\n\n")
            f.write("### FINE-TUNED (v1, iter-160)\n\n````\n" + ft[i].strip() + "\n````\n\n")
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    main()
