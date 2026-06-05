#!/usr/bin/env python3
"""Diagnostic: iter-400 checkpoint (nearest saved low-val to the 360 dip).
Goal: (1) do the v2@480 regressions (#1 module.xml, #12 repo) clear at 400,
(2) does it hold the wins, (3) does it work on fresh realistic generic tasks.
Loads base+adapter ONCE. Truncates each output at <|im_end|> for clean reads.
Writes ./eval/diagnostic-400.md.
"""
import os, time, gc
import mlx.core as mx
from mlx_lm import load, generate

BASE = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
ADAPTER = "/tmp/best-adapter-400"
MAXTOK = 380
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "diagnostic-400.md")

# (bucket, prompt)
PROMPTS = [
    # --- regression checks (v2@480 got these wrong) ---
    ("REGRESSION", "Create the registration.php and module.xml for a module Vendor_Catalog."),
    ("REGRESSION", "Load products with the repository and SearchCriteriaBuilder."),
    # --- hold-the-win checks ---
    ("HOLD-WIN", "Build a Hyva accordion component with Alpine.js and Tailwind."),
    ("HOLD-WIN", "Set up a cron job that runs hourly."),
    ("HOLD-WIN", "When should I use a plugin versus a preference?"),
    ("HOLD-WIN", "Create an observer for catalog_product_save_after."),
    ("HOLD-WIN", "Create a ViewModel and use it in a Hyva .phtml template."),
    # --- fresh realistic tasks (generic; a representative 'year of work' spread) ---
    ("REALISTIC", "Create an admin UI component grid (listing) for a custom entity with a data provider."),
    ("REALISTIC", "Add a custom product attribute via a data patch and display it on the product page."),
    ("REALISTIC", "Create a custom shipping method (carrier) in Magento 2."),
    ("REALISTIC", "Add a customer-scoped GraphQL query that returns the logged-in customer's records."),
    ("REALISTIC", "Write a console command that reindexes a custom indexer."),
    ("REALISTIC", "Add a system.xml configuration field and read it with ScopeConfig in a service."),
    ("REALISTIC", "Write a before plugin that adjusts the quantity before a product is added to the cart."),
]


def clear_cache():
    for fn in (getattr(mx, "clear_cache", None),
               getattr(getattr(mx, "metal", None), "clear_cache", None)):
        try:
            if fn:
                fn()
        except Exception:
            pass


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    print("Loading v2@400 (base + iter-400 adapter)...", flush=True)
    model, tok = load(BASE, adapter_path=ADAPTER)

    # make generation stop on <|im_end|> (Qwen turn terminator) where supported
    try:
        im_end = tok.convert_tokens_to_ids("<|im_end|>")
        if hasattr(tok, "eos_token_ids") and isinstance(tok.eos_token_ids, set):
            tok.eos_token_ids.add(im_end)
    except Exception as e:
        print(f"(eos-add skipped: {e})", flush=True)

    results = []
    for i, (bucket, p) in enumerate(PROMPTS, 1):
        t0 = time.time()
        ids = tok.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True)
        text = generate(model, tok, ids, max_tokens=MAXTOK, verbose=False)
        clean = text.split("<|im_end|>")[0].rstrip()  # drop the trailing artifact
        results.append((bucket, p, clean))
        print(f"[{bucket:10}] {i:2d}/{len(PROMPTS)} ({time.time()-t0:5.1f}s) {p[:46]}", flush=True)

    del model; gc.collect(); clear_cache()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Diagnostic — fine-tuned v2 @ iter-400 (val 1.326)\n\n")
        f.write(f"- Base: `{BASE}` + LoRA adapter `/tmp/best-adapter-400` (iter-400)\n")
        f.write("- Outputs truncated at `<|im_end|>` (clean answers; confirms the model terminates)\n")
        f.write("- Buckets: REGRESSION (v2@480 failed these), HOLD-WIN, REALISTIC (fresh generic tasks)\n\n")
        for i, (bucket, p, clean) in enumerate(results, 1):
            f.write(f"\n---\n\n## {i}. [{bucket}] {p}\n\n````\n{clean}\n````\n\n")
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    main()
