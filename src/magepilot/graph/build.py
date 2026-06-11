"""Incremental graph build (docs/architecture/05).

Flow: discovery (stat fast-path → sha1 only changed files; vendor gated by
registration.php per composer package) → module pass → extraction in 200-file
transactions (XML configs first — an interrupted build is already useful) → resolve
pass. `files.status` IS the resume checkpoint: Ctrl-C loses at most one transaction and
a restart picks up `WHERE status='pending'`. `meta.build_state` lets the query layer
warn when answering from a partial graph.
"""
import hashlib
import os
import re
import time

import defusedxml.ElementTree as ET

from magepilot.graph.extractors import EXTRACTORS, classify
from magepilot.graph.store import GraphStore, graph_path
from magepilot.graph import resolve

SKIP_DIRS = {".git", ".idea", ".github", "node_modules", "generated", "var", "pub",
             "dev", "setup", "tests", "Fixture", "_files", "__pycache__", "__MACOSX"}
# pruned only inside vendor/: app/code Test/ dirs carry unit + MFTF coverage the graph
# wants; vendor test trees are bulk we never query
VENDOR_SKIP_DIRS = {"Test"}
TXN_FILES = 200
MAX_FILE_BYTES = 1024 * 1024
# Bump when ANY extractor's output semantics change: existing graphs re-extract fully
# on the next build (file hashes can't see code changes).
EXTRACTOR_VERSION = "3"


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: str, vendor: bool):
    """Yield (rel, abs, ftype, area, in_vendor) for graph-relevant files."""
    root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        parts = [] if rel_dir == "." else rel_dir.split("/")
        in_vendor = parts[:1] == ["vendor"]
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                       and not (in_vendor and d in VENDOR_SKIP_DIRS)]
        if in_vendor:
            if not vendor:
                dirnames[:] = []
                continue
            # gate: only composer packages that register a Magento component
            # (registration.php at vendor/<a>/<b>/) — prunes symfony/laminas bulk
            if len(parts) == 3 and not os.path.isfile(os.path.join(dirpath, "registration.php")):
                dirnames[:] = []
                continue
        for name in filenames:
            rel = f"{rel_dir}/{name}" if parts else name
            ftype, area = classify(rel)
            if ftype is None:
                continue
            ab = os.path.join(dirpath, name)
            try:
                if os.path.getsize(ab) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield rel, ab, ftype, area, in_vendor


def _enabled_modules(root: str) -> dict[str, int]:
    """'Module_Name' => 1|0 pairs from app/etc/config.php (regex — no PHP runtime)."""
    path = os.path.join(root, "app", "etc", "config.php")
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {}
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"'([A-Za-z0-9]+_[A-Za-z0-9]+)'\s*=>\s*([01])", text)}


def build(root: str, *, vendor: bool = True, verbose: bool = True) -> dict:
    """Build/update the graph. Returns counts. Safe to interrupt and re-run."""
    root = os.path.realpath(root)
    store = GraphStore(graph_path(root))
    t0 = time.monotonic()
    store.set_meta("root", root)
    if store.get_meta("extractor_version") != EXTRACTOR_VERSION:
        # Extractor code changed since this graph was built — re-extract everything.
        # Wipe content wholesale: per-file deletes against a full FTS index are ~30x
        # slower than rebuilding into empty tables (FTS5 segment churn).
        store.db.execute("DELETE FROM edges")
        store.db.execute("DELETE FROM node_fts")
        store.db.execute("DELETE FROM nodes")
        store.db.execute("UPDATE files SET status='pending'")
        store.set_meta("extractor_version", EXTRACTOR_VERSION)
        store.set_meta("last_complete", "0")
        store.db.commit()
    store.set_meta("build_state", "discovering")

    # ---- discovery: stat fast-path, hash only changed files
    present, changed = set(), 0
    for rel, ab, ftype, area, in_vendor in _iter_files(root, vendor):
        present.add(rel)
        try:
            stt = os.stat(ab)
        except OSError:
            continue
        if store.stat_unchanged(rel, stt.st_mtime_ns, stt.st_size):
            continue
        _, did_change = store.upsert_file(rel, _sha1(ab), stt.st_mtime_ns, stt.st_size,
                                          ftype, area, in_vendor)
        changed += did_change
    deleted = store.delete_missing_files(present)
    store.db.commit()

    # ---- no-change short-circuit: a clean rerun skips extraction AND resolve
    if (changed == 0 and deleted == 0
            and store.get_meta("build_state") == "discovering"
            and not store.db.execute(
                "SELECT 1 FROM files WHERE status='pending' LIMIT 1").fetchone()):
        prev = store.get_meta("last_complete")
        if prev == "1":
            store.set_meta("build_state", "complete")
            store.db.commit()
            counts = {"modules": store.db.execute("SELECT COUNT(*) FROM modules").fetchone()[0],
                      "nodes": store.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                      "edges": store.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
                      "files": store.db.execute(
                          "SELECT COUNT(*) FROM files WHERE status='done'").fetchone()[0],
                      "errors": 0, "changed": 0, "deleted": 0,
                      "seconds": round(time.monotonic() - t0, 1)}
            if verbose:
                print(f"✓ graph up to date ({counts['nodes']} nodes, {counts['seconds']}s)")
            store.close()
            return counts

    # ---- module pass: every etc/module.xml defines a module root
    enabled = _enabled_modules(root)
    for row in store.db.execute("SELECT path, in_vendor FROM files WHERE ftype='module'"):
        mroot = row["path"][:-len("/etc/module.xml")].rstrip("/") or "."
        try:
            mod = ET.parse(os.path.join(root, row["path"])).getroot().find("module")
            name = (mod.get("name") or "").strip() if mod is not None else ""
        except Exception:
            name = ""
        if not name:
            continue
        composer = ""
        cpath = os.path.join(root, mroot, "composer.json")
        if os.path.isfile(cpath):
            m = re.search(r'"name"\s*:\s*"([^"]+)"', open(cpath, encoding="utf-8",
                                                          errors="replace").read())
            composer = m.group(1) if m else ""
        store.upsert_module(name, mroot, bool(row["in_vendor"]), composer,
                            enabled.get(name))
    store.db.commit()

    # ---- extraction in transactions (resumable via files.status)
    store.set_meta("build_state", "extracting")
    store.db.commit()
    pending = store.pending_files()
    total, done, errors = len(pending), 0, 0
    for i in range(0, total, TXN_FILES):
        batch = pending[i:i + TXN_FILES]
        for row in batch:
            store.begin_file(row["id"])
            module_id = store.module_for_path(row["path"])
            if module_id is not None:
                store.db.execute("UPDATE files SET module_id=? WHERE id=?",
                                 (module_id, row["id"]))
            try:
                EXTRACTORS[row["ftype"]](store, row["id"], os.path.join(root, row["path"]),
                                         row["path"], row["area"], module_id)
                store.mark_file(row["id"], "done")
            except Exception as e:        # quarantine the file, never abort the build
                store.mark_file(row["id"], "error", str(e)[:300])
                errors += 1
            done += 1
        store.db.commit()
        if verbose and total:
            print(f"\r  graph: {done}/{total} files ({errors} errors)", end="", flush=True)

    # ---- resolve
    store.set_meta("build_state", "resolving")
    store.db.commit()
    resolve.run(store)
    store.set_meta("build_state", "complete")
    store.set_meta("last_complete", "1")
    store.set_meta("built_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    store.db.commit()

    counts = {k: store.db.execute(q).fetchone()[0] for k, q in {
        "modules": "SELECT COUNT(*) FROM modules",
        "nodes": "SELECT COUNT(*) FROM nodes",
        "edges": "SELECT COUNT(*) FROM edges",
        "files": "SELECT COUNT(*) FROM files WHERE status='done'",
        "errors": "SELECT COUNT(*) FROM files WHERE status='error'",
    }.items()}
    counts.update(changed=changed, deleted=deleted,
                  seconds=round(time.monotonic() - t0, 1))
    if verbose:
        print(f"\r✓ graph: {counts['nodes']} nodes, {counts['edges']} edges, "
              f"{counts['modules']} modules from {counts['files']} files "
              f"({counts['errors']} errors, {counts['seconds']}s)" + " " * 10)
    store.close()
    return counts
