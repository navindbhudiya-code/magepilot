"""GraphStore — connection management and the write helpers extractors use.

Per-file replace is the idempotency unit: re-extracting a file first deletes its nodes
(cascading its edges) and re-inserts. The FTS index is maintained explicitly (no
triggers) so a crash can't silently desync it — the resolve pass re-syncs changed rows.
"""
import hashlib
import json
import os
import re
import sqlite3

from magepilot import config
from magepilot.graph.schema import DDL, SCHEMA_VERSION

GRAPH_FILENAME = "graph.db"


def _project_dir(root: str) -> str:
    h = hashlib.sha1(os.path.realpath(root).encode()).hexdigest()[:16]
    return os.path.join(config.CODE_CHROMA_PATH, h)


def graph_path(root: str) -> str:
    return os.path.join(_project_dir(root), GRAPH_FILENAME)


def _split_words(name: str) -> str:
    """'ProductRepositoryInterface' → 'product repository interface' (FTS recall)."""
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", name)
    return " ".join(p.lower() for p in parts)


class GraphStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL)
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ------------------------------------------------------------------ modules
    def upsert_module(self, name: str, path: str, in_vendor: bool,
                      composer: str = "", enabled=None) -> int:
        self.db.execute(
            "INSERT INTO modules(name,path,in_vendor,composer,enabled) VALUES(?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET path=excluded.path, in_vendor=excluded.in_vendor, "
            "composer=excluded.composer, enabled=COALESCE(excluded.enabled, modules.enabled)",
            (name, path, int(in_vendor), composer, enabled))
        return self.db.execute("SELECT id FROM modules WHERE name=?", (name,)).fetchone()["id"]

    def module_for_path(self, path: str) -> int | None:
        """Longest-prefix module match for a file path."""
        row = self.db.execute(
            "SELECT id FROM modules WHERE ? LIKE path || '/%' OR path='.' ORDER BY length(path) DESC LIMIT 1",
            (path,)).fetchone()
        return row["id"] if row else None

    # ------------------------------------------------------------------ files
    def upsert_file(self, path: str, sha1: str, mtime_ns: int, size: int,
                    ftype: str, area: str | None, in_vendor: bool) -> tuple[int, bool]:
        """Returns (file_id, changed). Unchanged files keep status='done'."""
        row = self.db.execute("SELECT id, sha1 FROM files WHERE path=?", (path,)).fetchone()
        if row and row["sha1"] == sha1:
            return row["id"], False
        if row:
            self.db.execute(
                "UPDATE files SET sha1=?, mtime_ns=?, size=?, ftype=?, area=?, in_vendor=?, "
                "status='pending', error=NULL WHERE id=?",
                (sha1, mtime_ns, size, ftype, area, int(in_vendor), row["id"]))
            return row["id"], True
        cur = self.db.execute(
            "INSERT INTO files(path,sha1,mtime_ns,size,ftype,area,in_vendor) VALUES(?,?,?,?,?,?,?)",
            (path, sha1, mtime_ns, size, ftype, area, int(in_vendor)))
        return cur.lastrowid, True

    def stat_unchanged(self, path: str, mtime_ns: int, size: int) -> bool:
        """Fast path: a done file with identical mtime+size never gets re-hashed."""
        row = self.db.execute(
            "SELECT 1 FROM files WHERE path=? AND mtime_ns=? AND size=? AND status='done'",
            (path, mtime_ns, size)).fetchone()
        return row is not None

    def delete_missing_files(self, present: set[str]) -> int:
        """Drop DB rows for files that vanished from disk (cascade cleans nodes/edges)."""
        gone = [r["path"] for r in self.db.execute("SELECT path FROM files")
                if r["path"] not in present]
        for p in gone:
            fid = self.db.execute("SELECT id FROM files WHERE path=?", (p,)).fetchone()["id"]
            self._delete_file_nodes(fid)
            self.db.execute("DELETE FROM files WHERE id=?", (fid,))
        return len(gone)

    def pending_files(self) -> list[sqlite3.Row]:
        # XML configs before PHP: cheap, high-value first — an interrupted build is useful.
        return self.db.execute(
            "SELECT * FROM files WHERE status='pending' "
            "ORDER BY CASE ftype WHEN 'module' THEN 0 WHEN 'registration' THEN 0 "
            "WHEN 'di' THEN 1 WHEN 'events' THEN 1 ELSE 2 END, path").fetchall()

    def mark_file(self, file_id: int, status: str, error: str = None) -> None:
        self.db.execute("UPDATE files SET status=?, error=?, indexed_at=strftime('%s','now') "
                        "WHERE id=?", (status, error, file_id))

    def _delete_file_nodes(self, file_id: int) -> None:
        ids = [r["id"] for r in self.db.execute(
            "SELECT id FROM nodes WHERE file_id=?", (file_id,))]
        if ids:
            qs = ",".join("?" * len(ids))
            self.db.execute(f"DELETE FROM node_fts WHERE rowid IN ({qs})", ids)
            self.db.execute(f"DELETE FROM nodes WHERE id IN ({qs})", ids)
        # edges DECLARED in this file but whose src node lives elsewhere (di.xml edges)
        self.db.execute("DELETE FROM edges WHERE file_id=?", (file_id,))

    def begin_file(self, file_id: int) -> None:
        """Idempotent per-file replace: clear everything this file produced."""
        self._delete_file_nodes(file_id)

    # ------------------------------------------------------------------ nodes/edges
    def add_node(self, kind: str, name: str, qname: str, file_id: int = None,
                 module_id: int = None, line_start: int = None, line_end: int = None,
                 signature: str = None, attrs: dict = None) -> int:
        cur = self.db.execute(
            "INSERT INTO nodes(kind,name,qname,module_id,file_id,line_start,line_end,signature,attrs) "
            "VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(kind,qname) DO UPDATE SET name=excluded.name, "
            "module_id=COALESCE(excluded.module_id, nodes.module_id), "
            "file_id=COALESCE(excluded.file_id, nodes.file_id), "
            "line_start=COALESCE(excluded.line_start, nodes.line_start), "
            "line_end=COALESCE(excluded.line_end, nodes.line_end), "
            "signature=COALESCE(excluded.signature, nodes.signature), "
            "attrs=COALESCE(excluded.attrs, nodes.attrs)",
            (kind, name, qname, module_id, file_id, line_start, line_end, signature,
             json.dumps(attrs) if attrs else None))
        nid = self.db.execute("SELECT id FROM nodes WHERE kind=? AND qname=?",
                              (kind, qname)).fetchone()["id"]
        self.db.execute("DELETE FROM node_fts WHERE rowid=?", (nid,))
        self.db.execute("INSERT INTO node_fts(rowid,name,qname,words) VALUES(?,?,?,?)",
                        (nid, name, qname, _split_words(name)))
        return nid

    def ensure_node(self, kind: str, qname: str) -> int:
        """A placeholder node for a referenced-but-not-yet-seen symbol (e.g. an event
        name or a vendor class outside the indexed set)."""
        name = qname.rsplit("\\", 1)[-1].rsplit(":", 1)[-1]
        return self.add_node(kind, name, qname)

    def add_edge(self, kind: str, src_qname: str, dst_qname: str, file_id: int = None,
                 line: int = None, area: str = "global", attrs: dict = None,
                 src_id: int = None, dst_id: int = None) -> None:
        self.db.execute(
            "INSERT INTO edges(kind,src_id,src_qname,dst_id,dst_qname,file_id,line,area,attrs) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, src_id, src_qname, dst_id, dst_qname, file_id, line, area,
             json.dumps(attrs) if attrs else None))


def get_graph(root: str, create: bool = False) -> GraphStore | None:
    """Open the project's graph; None when it hasn't been built (and create=False)."""
    p = graph_path(root)
    if not create and not os.path.exists(p):
        return None
    return GraphStore(p)
