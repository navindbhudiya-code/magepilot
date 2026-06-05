"""Magepilot — a local, RAG-grounded AI agent for Magento 2.

Packages:
  codebase_index  build a semantic index of a Magento codebase
  tools           sandboxed tools the agent can call (search/read/grep/cli/kb)
  react_agent     the think -> act -> observe -> iterate loop
  cli             command-line entrypoint (`python -m agent.cli ...`)
"""
__all__ = ["config", "codebase_index", "tools", "react_agent"]
