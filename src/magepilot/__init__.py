"""MagePilot — a local, RAG-grounded AI agent for Magento 2 & Hyvä.

Packages:
  config    constants + TOML config loading (providers, roles, limits)
  llm       OpenAI-compatible client + role→model router
  tools     the tool framework (Tool/registry/parsing) and the sandboxed tools
  safety    path sandbox + the 3-tier command policy + approvers
  agent     the ReAct think → act → observe loop
  edits     approval-gated file scaffolding with undo
  magento   read-only DB access + git-change → command suggestions
  index     per-project semantic code index (ChromaDB)
  ui        CLI + interactive REPL
"""
__version__ = "0.2.0"
