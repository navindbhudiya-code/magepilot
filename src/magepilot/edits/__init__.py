"""Write-capable, approval-gated file changes — MagePilot can create/edit/delete files in
your project, but ONLY after you approve each change. Everything is sandboxed to the
project root.

Split (from v1 agent/edits.py):
  blocks    the @@CREATE/@@EDIT/@@MKDIR/@@DELETE plan format: parsing + edit application
  apply     previews, applying ops, the undo journal
  scaffold  plan generation (the Magento fine-tune via the 'coder' role) + run_make
"""
from magepilot.edits.apply import apply, preview, undo                       # noqa: F401
from magepilot.edits.blocks import _apply_edit, _clean, parse_plan           # noqa: F401
from magepilot.edits.scaffold import (                                        # noqa: F401
    _context, _extract, clarify, complete_manifest, generate_plan, render_ops,
    repair_plan, run_make,
)
