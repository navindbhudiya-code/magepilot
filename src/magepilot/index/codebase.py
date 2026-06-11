"""#1 Codebase indexing — build a semantic index of a Magento codebase into ChromaDB.

Walks the codebase, splits each source file into overlapping line-windows, embeds each
window (MiniLM-ONNX, local/CPU) and upserts into a persistent Chroma collection. Chunk ids
are a stable hash of (relpath + start_line), so re-indexing upserts in place (idempotent).

Run:  python -m magepilot index --root /path/to/magento
"""
import hashlib
import os

import chromadb
from chromadb.utils import embedding_functions

from magepilot import config

# Each project (directory) gets its OWN index, stored under CODE_CHROMA_PATH/<hash-of-path>/.
# So you can use magepilot in any folder and it keeps a separate index per project (like Claude Code).
_collections = {}   # cache: realpath(root) -> chroma collection


def _project_dir(root: str) -> str:
    h = hashlib.sha1(os.path.realpath(root).encode()).hexdigest()[:16]
    return os.path.join(config.CODE_CHROMA_PATH, h)


def get_code_collection(root: str):
    """Open the persistent code collection for THIS project (MiniLM-ONNX embedder)."""
    rp = os.path.realpath(root)
    if rp not in _collections:
        path = _project_dir(root)
        os.makedirs(path, exist_ok=True)
        client = chromadb.PersistentClient(path=path)
        ef = embedding_functions.DefaultEmbeddingFunction()
        _collections[rp] = client.get_or_create_collection(config.CODE_COLLECTION, embedding_function=ef)
    return _collections[rp]


def _reset_collection(root: str):
    """Drop and recreate this project's collection so its index holds exactly the current files."""
    rp = os.path.realpath(root)
    path = _project_dir(root)
    os.makedirs(path, exist_ok=True)
    client = chromadb.PersistentClient(path=path)
    try:
        client.delete_collection(config.CODE_COLLECTION)
    except Exception:
        pass
    ef = embedding_functions.DefaultEmbeddingFunction()
    _collections[rp] = client.get_or_create_collection(config.CODE_COLLECTION, embedding_function=ef)
    with open(os.path.join(path, "root.txt"), "w", encoding="utf-8") as f:
        f.write(rp)               # record which project this index belongs to
    return _collections[rp]


def is_indexed(root: str) -> bool:
    """True if THIS project has been indexed."""
    return os.path.exists(os.path.join(_project_dir(root), "root.txt"))


def iter_source_files(root: str):
    """Yield indexable source files under root, skipping vendor/generated/etc."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in config.SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(config.INDEX_EXTENSIONS):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > config.MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def chunk_file(path: str, root: str) -> list[dict]:
    """Split one file into overlapping line-window chunks with line metadata."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    if not any(ln.strip() for ln in lines):
        return []

    rel = os.path.relpath(path, root)
    step = max(1, config.CHUNK_LINES - config.CHUNK_OVERLAP)
    chunks = []
    for start in range(0, max(1, len(lines)), step):
        window = lines[start:start + config.CHUNK_LINES]
        body = "\n".join(window).strip()
        if not body:
            continue
        end = start + len(window)
        chunks.append({
            "rel": rel,
            "start": start + 1,        # 1-based, inclusive
            "end": end,                # 1-based, inclusive
            "text": body,
        })
        if end >= len(lines):
            break
    return chunks


def build_index(root: str, verbose: bool = True) -> int:
    """Index every source file under root. Returns the number of chunks written."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"codebase root not found: {root}")

    col = _reset_collection(root)      # fresh index for THIS project
    ids, docs, metas = [], [], []
    n_files = 0
    for path in iter_source_files(root):
        n_files += 1
        if verbose and n_files % 250 == 0:
            print(f"\r  scanning… {n_files} files", end="", flush=True)
        for ch in chunk_file(path, root):
            cid = hashlib.sha1(f"{ch['rel']}:{ch['start']}".encode()).hexdigest()[:16]
            ids.append(cid)
            docs.append(f"// {ch['rel']} (lines {ch['start']}-{ch['end']})\n{ch['text']}")
            metas.append({"rel": ch["rel"], "start": ch["start"], "end": ch["end"]})

    if verbose:
        print(f"\r  {n_files} files → {len(ids)} chunks; embedding (this can take a few minutes)…")
    # Upsert in batches (Chroma caps batch size) — show progress, embedding is the slow part.
    BATCH = 256
    for i in range(0, len(ids), BATCH):
        col.upsert(ids=ids[i:i + BATCH], documents=docs[i:i + BATCH], metadatas=metas[i:i + BATCH])
        if verbose:
            pct = int(100 * min(i + BATCH, len(ids)) / max(1, len(ids)))
            print(f"\r  embedding… {min(i + BATCH, len(ids))}/{len(ids)} chunks ({pct}%)", end="", flush=True)

    _save_root(root)
    if verbose:
        print(f"\r✓ indexed {len(ids)} chunks from {n_files} files → {_project_dir(root)}" + " " * 12)
    return len(ids)


def _save_root(root: str) -> None:
    os.makedirs(os.path.dirname(config.ROOT_MARKER), exist_ok=True)
    with open(config.ROOT_MARKER, "w", encoding="utf-8") as f:
        f.write(root)


def indexed_root() -> str | None:
    """The codebase root saved at the last index build (or None)."""
    try:
        with open(config.ROOT_MARKER, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None
