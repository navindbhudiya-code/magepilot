"""Semantic retrieval over the Magento knowledge base (ChromaDB)."""
import chromadb
from chromadb.utils import embedding_functions

import config

_collection = None


def get_collection():
    """Lazily open the persistent Chroma collection with the default (MiniLM-ONNX) embedder."""
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        ef = embedding_functions.DefaultEmbeddingFunction()
        _collection = client.get_or_create_collection(config.COLLECTION, embedding_function=ef)
    return _collection


def retrieve(query: str, k: int = None) -> list[dict]:
    """Return the top-k knowledge chunks for a query: [{text, source, title, distance}, ...]."""
    k = k or config.TOP_K
    col = get_collection()
    if col.count() == 0:
        return []
    res = col.query(query_texts=[query], n_results=min(k, col.count()))
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    return [
        {"text": d, "source": m.get("source"), "title": m.get("title"), "distance": dist}
        for d, m, dist in zip(docs, metas, dists)
    ]
