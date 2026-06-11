"""Knowledge-base lookup tool — searches the curated Magento facts store (separate from
the per-project code index). Migrated verbatim from agent/tools.py."""
import chromadb
from chromadb.utils import embedding_functions

from magepilot import config
from magepilot.tools.base import Param, RiskLevel, Tool, clip as _clip, root_tool


def kb_search(root: str, query: str, k: int = None) -> str:
    """Search the curated Magento knowledge base (facts/APIs) — separate from the code index."""
    try:
        client = chromadb.PersistentClient(path=config.KB_CHROMA_PATH)
        ef = embedding_functions.DefaultEmbeddingFunction()
        col = client.get_or_create_collection(config.KB_COLLECTION, embedding_function=ef)
    except Exception as e:
        return f"knowledge base unavailable: {e}"
    if col.count() == 0:
        return "knowledge base is empty (run `python rag/ingest.py`)"
    res = col.query(query_texts=[query], n_results=min(k or config.TOP_K_KB, col.count()))
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    if not docs:
        return f"no knowledge found for '{query}'"
    # Never expose the knowledge-base filename (private datasource): label by title only,
    # falling back to a generic heading rather than the .md source.
    blocks = [f"--- {m.get('title') or 'Magento reference'}\n{d}" for d, m in zip(docs, metas)]
    return _clip("\n\n".join(blocks))


TOOLS = (
    Tool(
        name="kb_search", fn=root_tool(kb_search), primary="query", risk=RiskLevel.READ,
        params=(Param("query", required=True, description="a Magento question"),
                Param("k", type="integer", description="number of results")),
        description="Look up Magento facts/APIs in the curated knowledge base (not your code). "
                    "Input: a question like 'how does a plugin differ from a preference'.",
    ),
)
