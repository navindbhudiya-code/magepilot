"""Write tools — MUTATE/DANGEROUS-tier file operations wrapping the edits primitives.

Every call is sandboxed (edits.apply → _safe_path), captures its reverse BEFORE applying,
and appends it to the undo journal so `magepilot undo` reverts the whole batch. The
registry's permission gate fronts these: no approver → denied; delete never accepts a
remembered "always".

These tools are NOT in the default ReAct catalog (READ-only); they exist for the
orchestrator and future modes that explicitly enable writes.
"""
import json
import os

from magepilot import config
from magepilot.edits.apply import _reverse_for, apply as _apply_op
from magepilot.tools.base import Param, RiskLevel, Tool, ToolContext


def _record_undo(root: str, reverse: dict | None) -> None:
    """Append one reverse op to the undo journal, extending it when the journal already
    belongs to this root (so a multi-write run undoes as one batch)."""
    if reverse is None:
        return
    root_real = os.path.realpath(root)
    journal = {"root": root_real, "ops": []}
    try:
        with open(config.UNDO_FILE, encoding="utf-8") as f:
            existing = json.load(f)
        if existing.get("root") == root_real:
            journal = existing
    except (OSError, json.JSONDecodeError):
        pass
    journal["ops"].append(reverse)
    os.makedirs(os.path.dirname(config.UNDO_FILE), exist_ok=True)
    with open(config.UNDO_FILE, "w", encoding="utf-8") as f:
        json.dump(journal, f)


def _do(ctx: ToolContext, op: dict) -> str:
    reverse = _reverse_for(ctx.root, op)
    out = _apply_op(ctx.root, op)
    if not out.startswith(("skipped", "unknown")):
        _record_undo(ctx.root, reverse)
    return out


def write_file(ctx: ToolContext, path: str, content: str = "") -> str:
    return _do(ctx, {"op": "create", "path": path, "content": content})


def edit_file(ctx: ToolContext, path: str, find: str = "", replace: str = "") -> str:
    return _do(ctx, {"op": "edit", "path": path, "find": find, "replace": replace})


def make_dir(ctx: ToolContext, path: str) -> str:
    return _do(ctx, {"op": "mkdir", "path": path})


def delete_file(ctx: ToolContext, path: str) -> str:
    return _do(ctx, {"op": "delete", "path": path})


TOOLS = (
    Tool(
        name="write_file", fn=write_file, primary="path", risk=RiskLevel.MUTATE,
        params=(Param("path", required=True, description="relative file path"),
                Param("content", required=True, description="the complete file content")),
        description="Create or overwrite a file inside the project (sandboxed; undoable; "
                    "requires approval). Input: {\"path\": \"...\", \"content\": \"...\"}.",
    ),
    Tool(
        name="edit_file", fn=edit_file, primary="path", risk=RiskLevel.MUTATE,
        params=(Param("path", required=True, description="relative file path"),
                Param("find", required=True, description="exact text already in the file"),
                Param("replace", required=True, description="replacement text")),
        description="Edit a file in place by exact find/replace (indentation-tolerant; "
                    "undoable; requires approval). "
                    "Input: {\"path\": \"...\", \"find\": \"...\", \"replace\": \"...\"}.",
    ),
    Tool(
        name="make_dir", fn=make_dir, primary="path", risk=RiskLevel.MUTATE,
        params=(Param("path", required=True, description="relative directory path"),),
        description="Create a directory inside the project (undoable; requires approval).",
    ),
    Tool(
        name="delete_file", fn=delete_file, primary="path", risk=RiskLevel.DANGEROUS,
        params=(Param("path", required=True, description="relative file path"),),
        description="Delete a file inside the project (undoable; always asks — never "
                    "auto-approved).",
    ),
)
