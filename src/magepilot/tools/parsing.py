"""ReAct-text parsing: the regexes and argument coercion the 7B model's output goes
through (migrated from agent/react_agent.py and agent/tools.py). When a provider with
native tool-calling is routed to (Phase 6), a second strategy slots in beside this one —
the registry and Tool definitions don't change."""
import json
import re

ACTION_RE = re.compile(r"Action\s*:\s*(.+?)\s*[\r\n]+\s*Action\s*Input\s*:\s*(.*)", re.S)
FINAL_RE = re.compile(r"Final\s*Answer\s*:\s*(.*)", re.S)

# v1-compatible aliases (tests and the executor reference these names).
_ACTION_RE = ACTION_RE
_FINAL_RE = FINAL_RE


def _parse_args(arg: str, primary: str) -> dict:
    """Accept either a JSON object ({\"path\": ...}) or a plain string (mapped to the primary arg)."""
    arg = (arg or "").strip()
    if not arg:
        return {}
    if arg[0] == "{":
        try:
            obj = json.loads(arg)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {primary: arg.strip().strip('"').strip("'")}


def _first_arg(text: str) -> str:
    """Keep just the tool input: a leading JSON object, else the first non-empty line."""
    text = text.strip()
    if text.startswith("{"):
        depth, end = 0, None
        for i, c in enumerate(text):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end:
            return text[:end]
    return text.splitlines()[0].strip() if text.splitlines() else ""
