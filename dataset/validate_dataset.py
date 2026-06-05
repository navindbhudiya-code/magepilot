#!/usr/bin/env python3
"""
Validate a Magento 2 fine-tuning dataset BEFORE training.

Usage:
    python validate_dataset.py [data_dir]      # default dir: data

Looks for <data_dir>/train.jsonl and <data_dir>/valid.jsonl.
Exits non-zero if any hard ERROR is found.

The leak check is only as good as the denylist. Put your real client / company /
project / vendor names and domains (one per line, '#' for comments) in a file named
'forbidden.local' next to this script. That file is gitignored, so the real names
never enter version control. If it is missing/empty, this script raises an ERROR so
the leak check can't silently pass.
"""
import json, sys, os, re, random
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else HERE
MAX_SEQ_LEN = 2048          # match the --max-seq-length you'll train with
CHARS_PER_TOKEN = 3.5       # code/markdown is denser than prose; conservative estimate

# --- denylist: loaded from gitignored 'forbidden.local' (one term per line) ---
def load_forbidden():
    path = os.path.join(HERE, "forbidden.local")
    terms = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms, path

FORBIDDEN, FORBIDDEN_PATH = load_forbidden()

# Patterns that almost always mean a leak -> hard ERROR.
HARD = {
    "aws_key":     re.compile(r"AKIA[0-9A-Z]{16}"),
    "env_secret":  re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token|bearer)\s*[=:]\s*['\"]?[A-Za-z0-9/+_\-]{12,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
}

# Patterns worth a human look -> WARN (often false positives in Magento XML/dev examples).
SOFT = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "url":   re.compile(r"https?://[^\s\"'<>]+"),
    "ip":    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
}
# Substrings that make a SOFT hit safe to ignore (Magento/W3C/doc/local-dev boilerplate).
SOFT_ALLOW = ("magento.com", "adobe.com", "w3.org", "example.com", "example.org",
              "urn:magento", "schema.org", "localhost", ".test", "hyva.io")

errors, warns = [], []


def load(path):
    if not os.path.exists(path):
        errors.append(f"MISSING FILE: {path}")
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{path}:{i} invalid JSON ({e})")
                continue
            rows.append((i, obj))
    return rows


def check_shape(path, i, obj):
    if set(obj.keys()) - {"messages"}:
        warns.append(f"{path}:{i} unexpected top-level keys: {set(obj.keys()) - {'messages'}}")
    msgs = obj.get("messages")
    if not isinstance(msgs, list) or len(msgs) < 2:
        errors.append(f"{path}:{i} missing/short 'messages'")
        return None, None
    roles = [m.get("role") for m in msgs]
    if "user" not in roles or "assistant" not in roles:
        errors.append(f"{path}:{i} needs both a user and an assistant turn")
    # chat-format hygiene: should open on user and close on assistant
    if roles and (roles[0] != "user" or roles[-1] != "assistant"):
        warns.append(f"{path}:{i} role order is {roles} (expected user...assistant)")
    user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
    asst = next((m["content"] for m in msgs if m.get("role") == "assistant"), "")
    if not user.strip() or not asst.strip():
        errors.append(f"{path}:{i} empty user or assistant content")
    return user, asst


def scan_leaks(path, i, text):
    low = text.lower()
    for name in FORBIDDEN:
        if name and name.lower() in low:
            errors.append(f"{path}:{i} LEAK: contains forbidden term '{name}'")
    for label, pat in HARD.items():
        if pat.search(text):
            errors.append(f"{path}:{i} LEAK: matches {label}")
    for label, pat in SOFT.items():
        for m in pat.findall(text):
            hit = m if isinstance(m, str) else " ".join(m)
            if not any(a in hit.lower() for a in SOFT_ALLOW):
                warns.append(f"{path}:{i} review {label}: {hit[:60]}")


def norm(s):
    return re.sub(r"\s+", " ", s.strip().lower())


def main():
    if not FORBIDDEN:
        errors.append(
            f"DENYLIST EMPTY: create '{FORBIDDEN_PATH}' with your real client/company/"
            "vendor names+domains (one per line). Leak-by-name detection is OFF until you do."
        )

    train = load(os.path.join(DATA_DIR, "train.jsonl"))
    valid = load(os.path.join(DATA_DIR, "valid.jsonl"))

    train_instr, all_seen = set(), Counter()
    over_len = 0

    for path, rows in (("train.jsonl", train), ("valid.jsonl", valid)):
        for i, obj in rows:
            user, asst = check_shape(path, i, obj)
            if user is None:
                continue
            scan_leaks(path, i, user)
            scan_leaks(path, i, asst)
            full = user + "\n" + asst
            est_tokens = int(len(full) / CHARS_PER_TOKEN)
            if est_tokens > MAX_SEQ_LEN:
                over_len += 1
                warns.append(f"{path}:{i} ~{est_tokens} tokens > max_seq_len {MAX_SEQ_LEN} (will be truncated)")
            key = norm(user)
            all_seen[key] += 1
            if path == "train.jsonl":
                train_instr.add(key)
            elif key in train_instr:
                errors.append(f"valid.jsonl:{i} OVERLAP: this instruction also appears in train (inflates eval)")

    dupes = {k: c for k, c in all_seen.items() if c > 1}
    for k, c in list(dupes.items())[:20]:
        warns.append(f"duplicate instruction x{c}: {k[:60]}")

    print(f"\n=== DATASET VALIDATION: {DATA_DIR}/ ===")
    print(f"denylist terms : {len(FORBIDDEN)} (from {os.path.basename(FORBIDDEN_PATH)})")
    print(f"train examples : {len(train)}")
    print(f"valid examples : {len(valid)}")
    print(f"duplicates     : {len(dupes)}")
    print(f"over-length    : {over_len}")
    print(f"ERRORS         : {len(errors)}")
    print(f"WARNINGS       : {len(warns)}")

    if errors:
        print("\n--- ERRORS (must fix before training) ---")
        for e in errors[:60]:
            print("  " + e)
        if len(errors) > 60:
            print(f"  ...and {len(errors)-60} more")
    if warns:
        print("\n--- WARNINGS (eyeball these) ---")
        for w in warns[:40]:
            print("  " + w)
        if len(warns) > 40:
            print(f"  ...and {len(warns)-40} more")

    if train:
        print("\n--- 3 RANDOM SAMPLES (read them) ---")
        for i, obj in random.sample(train, min(3, len(train))):
            u = next(m["content"] for m in obj["messages"] if m["role"] == "user")
            a = next(m["content"] for m in obj["messages"] if m["role"] == "assistant")
            print(f"\n[train:{i}] USER: {u[:160]}")
            print(f"          ASST: {a[:240]}...")

    print("\n" + ("FAILED — fix ERRORS above, then re-run." if errors
                  else "PASSED — no hard errors. Review warnings, then train."))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
