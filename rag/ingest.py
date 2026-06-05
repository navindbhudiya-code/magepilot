"""Build the Magento knowledge vector store: chunk knowledge/*.md by '##' section -> embed -> ChromaDB.

Idempotent: chunk ids are a stable hash of (source + heading), so re-running upserts in place.
Run:  python rag/ingest.py
"""
import glob
import hashlib
import os
import re

import config
from retriever import get_collection


def chunk_markdown(text: str, source: str) -> list[tuple[str, str]]:
    """Split a markdown doc into (title, body) chunks by level-2 ('## ') headings."""
    intro = re.split(r"(?m)^##\s+", text)[0]
    m = re.search(r"(?m)^#\s+(.+)$", intro)
    doc_title = m.group(1).strip() if m else source
    chunks = []
    for part in re.split(r"(?m)^##\s+", text)[1:]:
        lines = part.splitlines()
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        if body:
            chunks.append((f"{doc_title} — {heading}", body))
    if not chunks and intro.strip():
        chunks.append((doc_title, intro.strip()))
    return chunks


def main() -> None:
    col = get_collection()
    ids, docs, metas = [], [], []
    files = sorted(glob.glob(os.path.join(config.KNOWLEDGE_DIR, "*.md")))
    for path in files:
        source = os.path.basename(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for title, body in chunk_markdown(text, source):
            cid = hashlib.sha1(f"{source}:{title}".encode()).hexdigest()[:16]
            ids.append(cid)
            docs.append(f"{title}\n\n{body}")
            metas.append({"source": source, "title": title})

    if ids:
        col.upsert(ids=ids, documents=docs, metadatas=metas)
    print(f"Ingested {len(ids)} chunks from {len(files)} docs in {config.KNOWLEDGE_DIR}")
    print(f"  collection '{config.COLLECTION}' now holds {col.count()} chunks at {config.CHROMA_PATH}")


if __name__ == "__main__":
    main()
