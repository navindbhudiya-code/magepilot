"""The Magento repository knowledge graph (docs/architecture/05) — MagePilot's moat.

Replaces brute-force grep for structural questions: "what plugins intercept this method?",
"who observes this event?", "what breaks if I change this interface?", "why is my plugin
not firing?". SQLite-backed, Magento-aware (DI areas are first-class), hash-incremental.

The graph.db co-locates with the ChromaDB code index in the per-project cache dir.
"""
from magepilot.graph.store import GraphStore, get_graph, graph_path  # noqa: F401
