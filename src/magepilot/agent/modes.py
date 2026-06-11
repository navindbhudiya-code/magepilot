"""Agent modes (docs/architecture/08, Phase 5): a mode is a prompt addendum + a
visible-tool subset + a budget profile. With ~20 READ tools registered, focus is what
keeps a 7B model routing well — each mode shows only the tools its job needs.

Modes never change the SAFETY rules: the permission gate, the linter, and the sandbox
apply identically everywhere. `autonomous` raises budgets, not permissions.
"""
from dataclasses import dataclass, field

from magepilot.config.schema import LimitsCfg

# The focused subsets. None = every READ tool (back-compat default).
_CORE = ("search", "search_code", "grep", "read_file", "find_files", "list_dir",
         "symbol", "wiring", "recall_memory")
_BUILD = _CORE + ("kb_search", "impact", "magento_cli", "git_status", "git_diff", "remember")
_DEBUG = _CORE + ("stack_trace", "magento_logs", "diagnose_plugin", "impact",
                  "sql_query", "magento_cli", "git_log", "git_blame", "git_diff")


@dataclass(frozen=True)
class Mode:
    name: str
    prompt: str
    tools: tuple | None = None              # visible-tool subset (None = all READ)
    limits: dict = field(default_factory=dict)   # LimitsCfg overrides


MODES = {m.name: m for m in (
    Mode("ask",
         "Answer the question precisely with citations. Do NOT propose file changes.",
         tools=_CORE + ("kb_search",),
         limits={"max_tasks": 1, "max_total_steps": 8}),
    Mode("code",
         "Implement the requested change with idiomatic Magento 2 + Hyvä code. "
         "Investigate before editing; verify after.",
         tools=_BUILD),
    Mode("architect",
         "Design, do not implement: produce a concrete plan naming exact files, classes, "
         "di.xml/events.xml entries and their areas. Use `impact` before recommending "
         "changes to shared classes. Do NOT propose file edits.",
         tools=_CORE + ("kb_search", "impact", "git_log"),
         limits={"max_task_steps": 12}),
    Mode("debug",
         "Find the root cause before touching anything: stack_trace → read the culprit → "
         "check the wiring (diagnose_plugin/wiring) → smallest possible fix.",
         tools=_DEBUG),
    Mode("review",
         "Review the changes critically: correctness, Magento conventions, security, "
         "performance. Cite file:line for every finding. Do NOT edit.",
         tools=_CORE + ("git_diff", "git_log", "git_blame", "impact"),
         limits={"max_tasks": 2}),
    Mode("refactor",
         "Refactor in small, verifiable steps. Run `impact` on every class you change "
         "BEFORE changing it; preserve public behavior; never widen scope.",
         tools=_BUILD + ("git_log", "git_blame")),
    Mode("test",
         "Write tests for existing behavior. Unit: read the class first and mock its "
         "constructor dependencies (`symbol` lists them), one test class per subject "
         "under Test/Unit/. Storefront: MFTF scaffolds and Playwright specs come from "
         "`magepilot testgen --kind mftf|playwright` using graph-derived selectors.",
         tools=_BUILD),
    Mode("autonomous",
         "Work the objective end to end. Investigate before editing, verify after every "
         "change, and prefer finishing a smaller correct result over a larger unverified one.",
         tools=None,
         limits={"max_total_steps": 60, "max_tasks": 12, "wall_clock_minutes": 45}),
)}

DEFAULT = "code"


def get(name: str | None) -> Mode:
    return MODES.get((name or DEFAULT).lower(), MODES[DEFAULT])


def apply_limits(mode: Mode, limits: LimitsCfg) -> LimitsCfg:
    if not mode.limits:
        return limits
    return LimitsCfg(**{**limits.__dict__, **mode.limits})
