#!/usr/bin/env python3
"""v2 eval: base vs fine-tuned (iter-480 adapter). Loads each model once.
Original 12 prompts (for v1->v2 comparison) + 8 NEW prompts (generalization test).
Writes ./eval/compare-v2.md. Mirrors mlx_lm.generate --max-tokens 400.
"""
import os, time, gc
import mlx.core as mx
from mlx_lm import load, generate

BASE = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
ADAPTER = "/tmp/best-adapter-v2"   # iter-480, val 1.197
MAXTOK = 400
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "compare-v2.md")

# (tag, prompt) — 'orig' = same as v1 eval, 'new' = unseen generalization probe
PROMPTS = [
    ("orig", "Create the registration.php and module.xml for a module Vendor_Catalog."),
    ("orig", "Write a plugin that modifies a product repository method, with the di.xml."),
    ("orig", "When should I use a plugin versus a preference?"),
    ("orig", "Create an observer for catalog_product_save_after."),
    ("orig", "Add a custom CLI command to Magento 2."),
    ("orig", "Set up a cron job that runs hourly."),
    ("orig", "Write a frontend controller for route vendor/index/view using the correct base."),
    ("orig", "Create a ViewModel and use it in a Hyva .phtml template."),
    ("orig", "Build a Hyva accordion component with Alpine.js and Tailwind."),
    ("orig", "Add a GraphQL query with schema.graphqls and a resolver."),
    ("orig", "Define a custom table with declarative schema (db_schema.xml)."),
    ("orig", "Load products with the repository and SearchCriteriaBuilder."),
    # --- NEW generalization probes ---
    ("new", "Build a Hyva tabs component with Alpine.js and Tailwind."),
    ("new", "Explain when to use a plugin, a preference, and an observer."),
    ("new", "Create a Magento cron job that runs daily at midnight using crontab.xml."),
    ("new", "Write a message queue publisher and consumer to sync an order asynchronously."),
    ("new", "Add a foreign key column to an existing table with declarative schema."),
    ("new", "Implement a repository getById method that throws NoSuchEntityException."),
    ("new", "Expose a service method as a REST endpoint in webapi.xml with an ACL resource."),
    ("new", "Fix an N+1 query when loading customers for a list of orders."),
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
    for i, (_tag, p) in enumerate(PROMPTS, 1):
        t0 = time.time()
        ids = tok.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True)
        text = generate(model, tok, ids, max_tokens=MAXTOK, verbose=False)
        print(f"[{label}] {i:2d}/{len(PROMPTS)} ({time.time()-t0:5.1f}s) {p[:48]}", flush=True)
        outs.append(text)
    return outs


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    print("Loading BASE model...", flush=True)
    model, tok = load(BASE)
    base = gen_all(model, tok, "base")
    del model; gc.collect(); clear_cache()

    print("Loading FINE-TUNED v2 (base + iter-480 adapter)...", flush=True)
    model, tok = load(BASE, adapter_path=ADAPTER)
    ft = gen_all(model, tok, "v2")
    del model; gc.collect(); clear_cache()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Base vs Fine-tuned v2 comparison\n\n")
        f.write(f"- Base model: `{BASE}`\n")
        f.write(f"- Fine-tuned v2: base + LoRA adapter `/tmp/best-adapter-v2` (iter-480, val loss 1.197)\n")
        f.write(f"- max_tokens: {MAXTOK}, greedy decoding\n")
        f.write("- Prompts 1-12 = same as v1 eval; 13-20 = NEW (unseen generalization probes)\n\n")
        for i, (tag, p) in enumerate(PROMPTS):
            f.write(f"\n---\n\n## Prompt {i+1} [{tag.upper()}]\n\n> {p}\n\n")
            f.write("### BASE\n\n````\n" + base[i].strip() + "\n````\n\n")
            f.write("### FINE-TUNED v2 (iter-480)\n\n````\n" + ft[i].strip() + "\n````\n\n")
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    main()
