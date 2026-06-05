#!/usr/bin/env python3
"""Option A: re-test the degeneration-prone prompts on iter-480 with SAMPLING
(temperature 0.3 + repetition_penalty 1.1) to see if the greedy repetition loops vanish.
Loads base+iter-480 once. Truncates at <|im_end|>. Writes ./eval/sampling-480.md.
"""
import os, time, gc
import mlx.core as mx
from mlx_lm import load, generate
from mlx_lm.sample_utils import make_sampler, make_logits_processors

BASE = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
ADAPTER = "/tmp/best-adapter-v2"   # iter-480
MAXTOK = 380
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "sampling-480.md")

PROMPTS = [
    ("LOOPED@greedy", "Load products with the repository and SearchCriteriaBuilder."),
    ("LOOPED@greedy", "Create an admin UI component grid (listing) for a custom entity with a data provider."),
    ("LOOPED@greedy", "Create a custom shipping method (carrier) in Magento 2."),
    ("STRESS-new",    "Create a custom payment method in Magento 2."),
    ("STRESS-new",    "Write a message queue publisher and consumer to sync an order asynchronously."),
    ("WRAPPER-check", "Create the registration.php and module.xml for a module Vendor_Catalog."),
    ("CONTROL",       "Build a Hyva accordion component with Alpine.js and Tailwind."),
    ("CONTROL",       "Implement a repository getById method that throws NoSuchEntityException."),
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
    mx.random.seed(0)  # reproducible sampling

    print("Loading iter-480 (base + adapter), sampling temp=0.3 rep_penalty=1.1...", flush=True)
    model, tok = load(BASE, adapter_path=ADAPTER)
    try:
        im_end = tok.convert_tokens_to_ids("<|im_end|>")
        if hasattr(tok, "eos_token_ids") and isinstance(tok.eos_token_ids, set):
            tok.eos_token_ids.add(im_end)
    except Exception as e:
        print(f"(eos-add skipped: {e})", flush=True)

    sampler = make_sampler(temp=0.3, top_p=0.95)
    logits_processors = make_logits_processors(repetition_penalty=1.1, repetition_context_size=20)

    results = []
    for i, (bucket, p) in enumerate(PROMPTS, 1):
        t0 = time.time()
        ids = tok.apply_chat_template([{"role": "user", "content": p}], add_generation_prompt=True)
        text = generate(model, tok, ids, max_tokens=MAXTOK,
                        sampler=sampler, logits_processors=logits_processors, verbose=False)
        clean = text.split("<|im_end|>")[0].rstrip()
        results.append((bucket, p, clean))
        # crude loop detector: any line repeated > 4x?
        lines = [l.strip() for l in clean.splitlines() if l.strip()]
        looped = any(lines.count(l) > 4 for l in set(lines)) if lines else False
        print(f"[{bucket:13}] {i}/8 ({time.time()-t0:5.1f}s) loop={looped}  {p[:42]}", flush=True)

    del model; gc.collect(); clear_cache()

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# Sampling test — iter-480 (temp 0.3, repetition_penalty 1.1)\n\n")
        f.write(f"- Base `{BASE}` + adapter `/tmp/best-adapter-v2` (iter-480)\n")
        f.write("- Goal: do the greedy-decoding repetition loops vanish under sampling?\n")
        f.write("- LOOPED@greedy = degenerated under greedy; STRESS-new = fresh complex; CONTROL = was fine\n\n")
        for i, (bucket, p, clean) in enumerate(results, 1):
            f.write(f"\n---\n\n## {i}. [{bucket}] {p}\n\n````\n{clean}\n````\n\n")
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    main()
