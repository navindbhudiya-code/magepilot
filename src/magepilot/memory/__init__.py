"""Context memory (docs/architecture/06): the agent stops rediscovering its own findings.

Three layers, one SQLite schema:
  session   the run transcript (already covered by checkpoints/events)
  project   ~/.cache/magepilot/projects/<sha1-of-root>/memory.db — auto-extracted facts
  global    ~/.magepilot/memory.db — user preferences, EXPLICIT-only writes (no project
            content ever auto-leaks into the shared store)
"""
from magepilot.memory.store import MemoryStore, global_store, project_store  # noqa: F401
