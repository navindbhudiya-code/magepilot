# Dataset tooling

Builds and validates the Magento 2 instruction→answer training set.

> **The dataset itself (`_data.py`, `*.jsonl`, `REPORT.md`) is released separately and is gitignored** —
> only the tooling is tracked here.

## Files
- `_data.py` *(private)* — the curated examples, one per `add("category", user, assistant)` call (raw
  triple-quoted strings so PHP namespaces/backslashes survive verbatim).
- `_build.py` — validate → dedup (by normalized instruction) → shuffle (seeded) → **90/10** split →
  write `{all,train,valid}.jsonl` + `REPORT.md` → forbidden-content scan.
- `validate_dataset.py` — the pre-train gate: JSON/shape, train↔valid overlap, sequence length, and a
  leak check (AWS keys, `password=`/`token=`, emails, private keys + a `forbidden.local` denylist).

## Run
```bash
python dataset/_build.py            # regenerate outputs; a clean run ends "0 suspicious fragment(s)"
python dataset/validate_dataset.py  # must report 0 ERRORS before training
```

## Privacy
Every example is **anonymized, generic Magento** (`Vendor\Module`, neutral method bodies; no client
names, secrets, SKUs, or PII). `forbidden.local` (gitignored, one term per line) is your real
client/vendor denylist — the validator fails if any term appears. Keep the dataset out of git and
release it on your own terms.
