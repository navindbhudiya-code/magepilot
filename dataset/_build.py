"""Build pipeline: validate -> dedup -> shuffle -> split -> report -> forbidden scan.
Reads EX from _data.py. Writes all.jsonl, train.jsonl, valid.jsonl, REPORT.md.
All training lines are pure {"messages":[...]} chat format.
"""
import json, re, random, collections, os

HERE = os.path.dirname(os.path.abspath(__file__))
from _data import EX  # list of {"category","messages":[user,assistant]}

def norm(s):
    return re.sub(r"\s+", " ", s.strip().lower())

# ---- 1. write all.jsonl (pure messages) + keep category sidecar ----
all_path = os.path.join(HERE, "all.jsonl")
with open(all_path, "w") as f:
    for e in EX:
        f.write(json.dumps({"messages": e["messages"]}, ensure_ascii=False) + "\n")
print(f"[1] wrote {len(EX)} raw examples -> all.jsonl")

# ---- 2. validate ----
valid, dropped = [], 0
for e in EX:
    m = e.get("messages")
    ok = (isinstance(m, list) and len(m) == 2
          and m[0].get("role") == "user" and m[0].get("content", "").strip()
          and m[1].get("role") == "assistant" and m[1].get("content", "").strip())
    try:
        json.loads(json.dumps({"messages": m}, ensure_ascii=False))
    except Exception:
        ok = False
    if ok:
        valid.append(e)
    else:
        dropped += 1
print(f"[2] validated: {len(valid)} kept, {dropped} dropped")

# ---- 3. dedup by normalized instruction ----
seen, deduped, dups = set(), [], 0
for e in valid:
    k = norm(e["messages"][0]["content"])
    if k in seen:
        dups += 1
        continue
    seen.add(k)
    deduped.append(e)
print(f"[3] dedup: {len(deduped)} kept, {dups} duplicates removed")

# ---- 4. category counts ----
cats = collections.Counter(e["category"] for e in deduped)
print("[4] per-category counts:")
for c, n in sorted(cats.items(), key=lambda x: -x[1]):
    print(f"      {c:28} {n}")

# ---- 5. shuffle + 90/10 split, no overlap ----
random.seed(42)
shuf = deduped[:]
random.shuffle(shuf)
cut = round(len(shuf) * 0.90)
train, valid_split = shuf[:cut], shuf[cut:]
train_keys = {norm(e["messages"][0]["content"]) for e in train}
valid_split = [e for e in valid_split if norm(e["messages"][0]["content"]) not in train_keys]
for name, data in (("train.jsonl", train), ("valid.jsonl", valid_split)):
    with open(os.path.join(HERE, name), "w") as f:
        for e in data:
            f.write(json.dumps({"messages": e["messages"]}, ensure_ascii=False) + "\n")
overlap = train_keys & {norm(e["messages"][0]["content"]) for e in valid_split}
print(f"[5] split: train={len(train)} valid={len(valid_split)} overlap={len(overlap)}")

# ---- 6. REPORT.md with 5 random samples ----
random.seed(7)
samples = random.sample(deduped, min(5, len(deduped)))
with open(os.path.join(HERE, "REPORT.md"), "w") as f:
    f.write("# Dataset Report\n\n")
    f.write(f"- Generated examples (raw): {len(EX)}\n")
    f.write(f"- Dropped (validation): {dropped}\n")
    f.write(f"- Duplicates removed: {dups}\n")
    f.write(f"- Final unique examples: {len(deduped)}\n")
    f.write(f"- train.jsonl: {len(train)}\n")
    f.write(f"- valid.jsonl: {len(valid_split)}\n")
    f.write(f"- train/valid overlap: {len(overlap)}\n\n")
    f.write("## Counts per category\n\n")
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        f.write(f"- {c}: {n}\n")
    f.write("\n## 5 random samples\n\n")
    for i, e in enumerate(samples, 1):
        f.write(f"### Sample {i} ({e['category']})\n\n")
        f.write(f"**User:** {e['messages'][0]['content']}\n\n")
        f.write("**Assistant:**\n\n````\n" + e["messages"][1]["content"] + "\n````\n\n")
print("[6] wrote REPORT.md with 5 samples")

# ---- 7. forbidden-content scan ----
ALLOW_URL = re.compile(
    r"https?://("
    r"localhost|example\.(com|org)|magento|adobe|hyva\.io|secure\.example"
    r"|www\.w3\.org/"                # standard XML-Schema namespace URIs (xsi)
    r"|[a-z0-9.-]+\.test/"           # reserved .test TLD = local-dev placeholder, never real
    r")",
    re.I,
)
patterns = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://[^\s\"'`)]+"),
    "api_key_like": re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|bearer)\s*[=:]\s*[\"']?[A-Za-z0-9/\-+_]{12,}"),
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}
flagged = []
for idx, e in enumerate(deduped):
    text = e["messages"][0]["content"] + "\n" + e["messages"][1]["content"]
    for label, pat in patterns.items():
        for mobj in pat.finditer(text):
            frag = mobj.group(0)
            if label == "url" and ALLOW_URL.match(frag):
                continue
            if label == "ip" and frag in ("127.0.0.1", "0.0.0.0", "255.255.255.255"):
                continue
            flagged.append((idx, e["category"], label, frag))
print(f"[7] forbidden-content scan: {len(flagged)} suspicious fragment(s)")
for idx, cat, label, frag in flagged:
    print(f"      #{idx} [{cat}] {label}: {frag!r}")
print("DONE")
