"""Build pipeline: validate -> dedup -> shuffle -> split -> report -> forbidden scan.
Reads EX from _data.py. Writes all.jsonl, train.jsonl, valid.jsonl, REPORT.md.
All training lines are pure {"messages":[...]} chat format — either
[user, assistant] (prose mode) or [system, user, assistant] (the make/@@-block
mode, where system is the verbatim scaffolding prompt the agent sends at inference).
"""
import json, re, random, collections, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
from _data import EX  # list of {"category","messages":[...]}

def norm(s):
    return re.sub(r"\s+", " ", s.strip().lower())

def user_turn(e):
    """The user message regardless of 2- or 3-message shape (dedup/split key)."""
    return next(m["content"] for m in e["messages"] if m["role"] == "user")

# ---- 1. write all.jsonl (pure messages) + keep category sidecar ----
all_path = os.path.join(HERE, "all.jsonl")
with open(all_path, "w") as f:
    for e in EX:
        f.write(json.dumps({"messages": e["messages"]}, ensure_ascii=False) + "\n")
print(f"[1] wrote {len(EX)} raw examples -> all.jsonl")

# ---- 2. validate ----
def _shape_ok(m):
    if not isinstance(m, list):
        return False
    roles = [x.get("role") for x in m]
    if roles not in (["user", "assistant"], ["system", "user", "assistant"]):
        return False
    return all((x.get("content") or "").strip() for x in m)

valid, dropped = [], 0
for e in EX:
    m = e.get("messages")
    ok = _shape_ok(m)
    try:
        json.loads(json.dumps({"messages": m}, ensure_ascii=False))
    except Exception:
        ok = False
    if ok:
        valid.append(e)
    else:
        dropped += 1
print(f"[2] validated: {len(valid)} kept, {dropped} dropped")

# ---- 2b. @@-block answers must round-trip through the edits parser ----
# (the agent parses the coder's output with parse_plan; an example whose blocks the
# parser would mangle teaches a broken contract)
# load blocks.py directly by path — importing the magepilot package would drag in the
# whole agent stack (tomllib etc.); blocks.py itself needs only re/textwrap
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "magepilot_blocks", os.path.join(os.path.dirname(HERE), "src", "magepilot", "edits", "blocks.py"))
_blocks = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_blocks)
parse_plan = _blocks.parse_plan

bad_blocks = []
for i, e in enumerate(valid):
    answer = e["messages"][-1]["content"]
    if "@@CREATE" not in answer and "@@EDIT" not in answer:
        continue
    ops = parse_plan(answer)
    if not ops:
        bad_blocks.append((i, e["category"], "parses to zero ops"))
        continue
    for op in ops:
        if op["op"] == "create" and not op.get("content", "").strip():
            bad_blocks.append((i, e["category"], f"empty create {op['path']}"))
        if op["op"] == "create":
            # _clean() strips fences and truncates at 'Why:' — content must survive intact
            rendered = op["content"]
            original = re.search(r"@@CREATE " + re.escape(op["path"]) + r"\n(.*?)\n@@END",
                                 answer, re.S)
            if original and original.group(1).strip() != rendered:
                bad_blocks.append((i, e["category"], f"lossy round-trip {op['path']}"))
if bad_blocks:
    for i, cat, why in bad_blocks:
        print(f"      BAD @@-example #{i} [{cat}]: {why}")
    raise SystemExit(f"[2b] {len(bad_blocks)} @@-block example(s) fail the parser round-trip")
n_blocks = sum(1 for e in valid if "@@" in e["messages"][-1]["content"])
print(f"[2b] @@-block round-trip: {n_blocks} block examples parse cleanly")

# ---- 2c. the dataset's MAKE_SYSTEM must match the agent's scaffolding prompt ----
try:
    import _data
    scaffold_src = open(os.path.join(os.path.dirname(HERE), "src", "magepilot", "edits",
                                     "scaffold.py"), encoding="utf-8").read()
    # the prompt is a concatenated string literal in source; compare content-insensitively
    flat = re.sub(r"\s+", " ", _data.MAKE_SYSTEM)
    src_flat = re.sub(r'"\s*\n\s*"|\\n|\s+', " ", scaffold_src)
    probe = re.sub(r"\s+", " ", "You are a Magento 2 + Hyvä scaffolding engine")
    if probe in flat and probe not in src_flat:
        print("[2c] WARNING: scaffold.py prompt not found — cannot verify MAKE_SYSTEM sync")
    else:
        # spot-check distinctive phrases survive in both
        for phrase in ("scaffolding engine", "composer.json", "NOTHING after the final @@END"):
            if phrase not in _data.MAKE_SYSTEM or phrase not in scaffold_src.replace('"\n    "', ""):
                print(f"[2c] WARNING: MAKE_SYSTEM may have drifted from scaffold.SYSTEM ({phrase!r})")
                break
        else:
            print("[2c] MAKE_SYSTEM matches the scaffold prompt's key phrases")
except Exception as e:
    print(f"[2c] WARNING: MAKE_SYSTEM sync check skipped: {e}")

# ---- 3. dedup by normalized instruction ----
seen, deduped, dups = set(), [], 0
for e in valid:
    k = norm(user_turn(e))
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
train_keys = {norm(user_turn(e)) for e in train}
valid_split = [e for e in valid_split if norm(user_turn(e)) not in train_keys]
for name, data in (("train.jsonl", train), ("valid.jsonl", valid_split)):
    with open(os.path.join(HERE, name), "w") as f:
        for e in data:
            f.write(json.dumps({"messages": e["messages"]}, ensure_ascii=False) + "\n")
overlap = train_keys & {norm(user_turn(e)) for e in valid_split}
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
        f.write(f"**User:** {user_turn(e)}\n\n")
        f.write("**Assistant:**\n\n````\n" + e["messages"][-1]["content"] + "\n````\n\n")
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
    text = "\n".join(m["content"] for m in e["messages"])
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
