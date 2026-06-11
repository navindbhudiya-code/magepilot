"""Recall: push (the Known-facts block injected at run start, ≤400 tokens — context is
precious) and pull (the recall_memory tool). Plus run-end fact extraction."""
from magepilot.memory.store import global_store, project_store

BLOCK_CHAR_CAP = 1600          # ≈400 tokens
MAX_FACTS = 8
MAX_EXTRACT = 5


def recall_block(root: str, objective: str) -> str:
    """Top-scored project + global facts as a prompt block ('' when nothing relevant)."""
    lines, used_proj, used_glob = [], [], []
    proj = project_store(root)
    try:
        for r in proj.search(objective, k=MAX_FACTS):
            lines.append(f"- {r['content']}" + (f" (source: {r['source']})" if r["source"] else ""))
            used_proj.append(r["id"])
        proj.touch(used_proj)
    finally:
        proj.close()
    glob = global_store()
    try:
        for r in glob.search(objective, k=max(0, MAX_FACTS - len(lines))):
            lines.append(f"- {r['content']}")
            used_glob.append(r["id"])
        glob.touch(used_glob)
    finally:
        glob.close()
    if not lines:
        return ""
    block = "Known project facts (from earlier sessions — trust but verify paths):\n"
    for ln in lines:
        if len(block) + len(ln) > BLOCK_CHAR_CAP:
            break
        block += ln + "\n"
    return block.rstrip()


def extract_facts(root: str, run) -> int:
    """Run-end: persist ≤5 durable facts from the finished run's task notes.
    Deterministic — notes are already compressed; we store the informative ones."""
    proj = project_store(root)
    saved = 0
    try:
        for t in run.plan:
            if saved >= MAX_EXTRACT:
                break
            if t.status != "done" or not t.note or len(t.note) < 20:
                continue
            kind = "file_role" if t.kind == "investigate" else "fact"
            if proj.add(f"[{t.kind}] {t.goal[:120]}: {t.note[:300]}",
                        kind=kind, source=run.run_id):
                saved += 1
    finally:
        proj.close()
    return saved
