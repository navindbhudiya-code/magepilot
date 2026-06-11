"""Shared exception types (top-level so safety/ and tools/ can both import them
without creating a package-init cycle)."""


class ToolError(Exception):
    """Raised inside a tool; callers convert it to an observation string."""
