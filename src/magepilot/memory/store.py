"""The facts store. Facts cite their source (file:line or run_id) so stale memory is
auditable; usage counters feed recall ranking and LRU eviction."""
import hashlib
import os
import sqlite3
import time

from magepilot import config

DDL = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'fact',     -- fact | file_role | decision | gotcha | preference
  key TEXT,
  content TEXT NOT NULL,
  source TEXT,                           -- file:line or run_id — auditable provenance
  created_at INTEGER, last_used INTEGER, uses INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_kind ON facts(kind);
"""

PROJECT_CAP = 500
GLOBAL_CAP = 100


class MemoryStore:
    def __init__(self, path: str, cap: int = PROJECT_CAP):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.cap = cap
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL)

    def add(self, content: str, kind: str = "fact", key: str = "", source: str = "") -> int:
        content = (content or "").strip()
        if not content:
            return 0
        dup = self.db.execute("SELECT id FROM facts WHERE content=?", (content,)).fetchone()
        if dup:
            return dup["id"]
        now = int(time.time())
        cur = self.db.execute(
            "INSERT INTO facts(kind,key,content,source,created_at,last_used) VALUES(?,?,?,?,?,?)",
            (kind, key, content, source, now, now))
        self._evict()
        self.db.commit()
        return cur.lastrowid

    def search(self, query: str, k: int = 8) -> list[sqlite3.Row]:
        """Keyword-overlap scoring + log(uses) + recency. Deterministic, no model."""
        terms = {t for t in (query or "").lower().replace("\\", " ").split() if len(t) > 2}
        rows = self.db.execute("SELECT * FROM facts").fetchall()
        scored = []
        now = time.time()
        for r in rows:
            text = (r["content"] + " " + (r["key"] or "")).lower()
            overlap = sum(1 for t in terms if t in text)
            score = (overlap * 10
                     + min(r["uses"], 8)
                     - (now - (r["last_used"] or 0)) / (30 * 86400))
            if overlap or r["kind"] == "preference":   # preferences always eligible
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:k]]

    def touch(self, ids: list[int]) -> None:
        now = int(time.time())
        for i in ids:
            self.db.execute("UPDATE facts SET uses=uses+1, last_used=? WHERE id=?", (now, i))
        self.db.commit()

    def all_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    def _evict(self) -> None:
        n = self.all_count()
        if n >= self.cap:
            self.db.execute(
                "DELETE FROM facts WHERE id IN (SELECT id FROM facts "
                "ORDER BY last_used ASC LIMIT ?)", (max(1, n - self.cap + 1),))
        # prune never-used facts older than 90 days
        self.db.execute("DELETE FROM facts WHERE uses=0 AND created_at < ?",
                        (int(time.time()) - 90 * 86400,))

    def close(self) -> None:
        self.db.close()


def project_store(root: str) -> MemoryStore:
    h = hashlib.sha1(os.path.realpath(root).encode()).hexdigest()[:16]
    return MemoryStore(os.path.join(config.CACHE_DIR, "projects", h, "memory.db"))


def global_store() -> MemoryStore:
    return MemoryStore(os.path.expanduser("~/.magepilot/memory.db"), cap=GLOBAL_CAP)
