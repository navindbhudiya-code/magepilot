"""Task planning — deterministic-first (docs/architecture/03).

1. TEMPLATE MATCH: a rule table maps recognizable Magento task shapes to pre-built plans
   with correct `done_when` checks. The 7B never plans what a template already knows.
2. LLM FALLBACK: the planner role emits a numbered plain-line format (NOT JSON):

       1. [investigate] Locate where shipping methods are filtered | done: file and method identified
       2. [edit] Add a plugin restricting methods for B2B group | done: files created
       3. [command] Run setup:di:compile | done: exit 0
       4. [verify] Confirm plugin registered via dev:di:info | done: plugin listed

   One regex parses it; max 6 tasks; closed kind set. Unparseable → one nudge → fall back
   to a single task (today's v1 behavior). Planning failure must never fail the run.
"""
import re

from magepilot.agent.state import TASK_KINDS, Task
from magepilot.edits.scaffold import _extract
from magepilot.llm.router import get_router

MAX_TASKS = 6

_LINE_RE = re.compile(
    r"^\s*\d+\.\s*\[(" + "|".join(TASK_KINDS) + r")\]\s*(.+?)\s*(?:\|\s*done:\s*(.+?)\s*)?$",
    re.I)

# Plain requests that clearly build something (single-task fallback picks `edit`).
_BUILDISH_RE = re.compile(
    r"^\s*(create|add|generate|scaffold|build|set ?up|new|implement|make)\b", re.I)

_CMD_RE = re.compile(r"\b((?:setup|cache|indexer|module|deploy|cron|dev|app):[a-z][a-z0-9:-]*"
                     r"(?:\s+[A-Za-z0-9:_./-]+)*|composer\s+[a-z-]+)")


# ------------------------------------------------------------------ templates
def _t(i, kind, goal, done="", command="", check=None) -> Task:
    return Task(id=i, kind=kind, goal=goal, done_when=done, command=command, check=check or {})


def _module_paths(objective: str) -> tuple[str | None, list[str]]:
    vendor, name = _extract(objective)
    if vendor and name:
        base = f"app/code/{vendor}/{name}"
        return f"{vendor}_{name}", [f"{base}/registration.php", f"{base}/etc/module.xml"]
    return None, []


def _tpl_create_module(objective, m):
    module, files = _module_paths(objective)
    tasks = [
        _t(1, "edit", f"Scaffold the Magento module for this request, with every file it "
                      f"needs: {objective}",
           done="all module files created"),
        _t(2, "command", "Register the new module", done="exit 0", command="setup:upgrade"),
    ]
    if files:
        tasks.append(_t(3, "verify", f"Confirm the {module} module files exist",
                        done="registration.php and module.xml present",
                        check={"files_exist": files}))
    return tasks


def _tpl_create_theme(objective, m):
    vendor, name = _extract(objective)
    tasks = [
        _t(1, "edit", f"Scaffold the Hyvä/Magento theme for this request: {objective}",
           done="theme files created"),
        _t(2, "command", "Register the new theme", done="exit 0", command="setup:upgrade"),
    ]
    if vendor and name:
        base = f"app/design/frontend/{vendor}/{name}"
        tasks.append(_t(3, "verify", f"Confirm the {vendor}/{name} theme files exist",
                        done="registration.php and theme.xml present",
                        check={"files_exist": [f"{base}/registration.php", f"{base}/theme.xml"]}))
    return tasks


def _tpl_add_plugin(objective, m):
    return [
        _t(1, "investigate", f"Locate the exact class and public method to intercept for: "
                             f"{objective}. Cite the file path and method signature.",
           done="target class + method identified"),
        _t(2, "edit", f"Create the plugin class and its di.xml entry for: {objective}",
           done="plugin class + di.xml created"),
        _t(3, "command", "Clear the config cache so the plugin is wired",
           done="exit 0", command="cache:clean config"),
    ]


def _tpl_add_observer(objective, m):
    return [
        _t(1, "investigate", f"Identify the exact event name to observe for: {objective}. "
                             f"Cite where it is dispatched if visible.",
           done="event name identified"),
        _t(2, "edit", f"Create the observer class and events.xml for: {objective}",
           done="observer + events.xml created"),
        _t(3, "command", "Clear the config cache so the observer is wired",
           done="exit 0", command="cache:clean config"),
    ]


def _tpl_add_cron(objective, m):
    return [
        _t(1, "edit", f"Create the cron job class and crontab.xml for: {objective}",
           done="cron class + crontab.xml created"),
        _t(2, "command", "Clear the config cache so the cron job is registered",
           done="exit 0", command="cache:clean config"),
    ]


def _tpl_debug(objective, m):
    has_trace = bool(re.search(r"#\d+\s+\S+\.php\(\d+\)|\.php(?::| on line )\d+", objective))
    locate = (f"Run the stack_trace tool on the error below to get the parsed frames and "
              f"the culprit file, then read_file the culprit around the cited line.\n"
              f"Error report: {objective}" if has_trace else
              f"Locate this error: {objective}. Start with magento_logs (exception.log) "
              f"to pull the actual trace, then run stack_trace on it and read_file the "
              f"culprit at the cited line.")
    return [
        _t(1, "investigate", locate, done="exact file:line of the cause identified"),
        _t(2, "investigate", "Identify the root cause: inspect the located code and its "
                             "wiring — use `symbol` for the class, `wiring` for its "
                             "plugins/preference, and `diagnose_plugin` if a plugin or "
                             "Interceptor is involved. Explain WHY the error occurs.",
           done="root cause explained with evidence"),
        _t(3, "edit", f"Apply the smallest correct fix for the root cause of: {objective}",
           done="fix applied"),
        _t(4, "verify", "Re-inspect the changed file to confirm the fix is in place and "
                        "consistent with the surrounding code.",
           done="fix confirmed in the file"),
    ]


def _tpl_create_tests(objective, m):
    return [
        _t(1, "investigate", f"Identify the exact class under test for: {objective}. "
                             f"Use `symbol` to get its FQCN, file, methods, and constructor "
                             f"dependencies (each dependency becomes a mock).",
           done="FQCN + constructor dependencies identified"),
        _t(2, "edit", f"Create the PHPUnit unit test for: {objective}. Mirror the class "
                      f"path under Test/Unit/, mock every constructor dependency in "
                      f"setUp(), and cover the public methods (arrange/act/assert).",
           done="test file created"),
        _t(3, "command", "Run the new tests", done="exit 0",
           command="vendor/bin/phpunit app/code"),
    ]


# (name, matcher, builder) — first match wins; matchers are deliberately conservative so
# anything ambiguous falls through to the LLM planner. create_tests precedes
# create_module so "create tests for the X module" plans tests, not a module.
_CREATE = r"(?:create|add|generate|scaffold|build|set ?up|new|make|write)"
TEMPLATES = [
    ("create_tests", re.compile(_CREATE + r"\b.*\b(?:unit )?tests?\b", re.I), _tpl_create_tests),
    ("create_theme", re.compile(_CREATE + r"\b.*\btheme\b", re.I), _tpl_create_theme),
    ("create_module", re.compile(_CREATE + r"\b.*\bmodule\b", re.I), _tpl_create_module),
    ("add_plugin", re.compile(_CREATE + r"\b.*\bplugin\b|intercept\b", re.I), _tpl_add_plugin),
    ("add_observer", re.compile(_CREATE + r"\b.*\bobserver\b|observe .*event", re.I), _tpl_add_observer),
    ("add_cron", re.compile(_CREATE + r"\b.*\bcron\b", re.I), _tpl_add_cron),
    ("debug", re.compile(r"\b(debug|fix|error|exception|stack trace|not working|broken)\b", re.I), _tpl_debug),
]


def match_template(objective: str):
    """(template_name, [Task]) or None."""
    for name, rx, builder in TEMPLATES:
        m = rx.search(objective)
        if m:
            return name, builder(objective, m)
    return None


# ------------------------------------------------------------------ LLM fallback
PLANNER_SYSTEM = """You are the planner of a Magento 2 coding agent. Decompose the user's \
objective into AT MOST 6 tasks, one per line, in EXACTLY this format and nothing else:

1. [investigate] <what to find out> | done: <how we know it's done>
2. [edit] <what files to create or change> | done: <how we know it's done>
3. [command] <which bin/magento command to run> | done: exit 0
4. [verify] <what to confirm afterwards> | done: <how we know it's done>

Allowed kinds: investigate, edit, command, verify. Rules: investigate BEFORE edit when the \
target code is unknown; one edit task per coherent change; only whitelisted Magento commands \
(setup:upgrade, setup:di:compile, cache:clean, indexer:reindex); no prose before or after \
the numbered lines.

Example for "Add loyalty points when an order is placed":
1. [investigate] Find the event fired on order placement and the order model involved | done: event name identified
2. [edit] Create the observer class and events.xml awarding loyalty points | done: files created
3. [command] cache:clean config | done: exit 0
4. [verify] Confirm the observer is registered for the event in events.xml | done: observer wired"""


def parse_llm_plan(text: str) -> list[Task] | None:
    """Parse the numbered-line format. None when nothing parseable was found."""
    tasks = []
    for line in (text or "").splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        kind, goal, done = m.group(1).lower(), m.group(2).strip(), (m.group(3) or "").strip()
        command = ""
        if kind == "command":
            cm = _CMD_RE.search(goal)
            command = cm.group(1).strip() if cm else ""
        tasks.append(Task(id=len(tasks) + 1, kind=kind, goal=goal, done_when=done,
                          command=command))
        if len(tasks) >= MAX_TASKS:
            break
    return tasks or None


def _single_task(objective: str) -> list[Task]:
    """Degrade to v1 behavior: one task, kind picked by a cheap heuristic."""
    kind = "edit" if _BUILDISH_RE.search(objective) else "investigate"
    return [Task(id=1, kind=kind, goal=objective, done_when="objective satisfied")]


NO_EDIT_MODES = ("ask", "architect", "review")


def plan(objective: str, complete=None, mode: str = "code") -> tuple[str, list[Task]]:
    """Build the task queue. Returns (template_name_or_empty, tasks). Never raises:
    template → LLM numbered-line fallback (one nudge) → single task. No-edit modes
    (ask/architect/review) always get a single investigate task — they never plan
    file changes regardless of how build-ish the objective sounds."""
    if mode in NO_EDIT_MODES:
        return "", [Task(id=1, kind="investigate", goal=objective,
                         done_when="question answered with citations")]
    hit = match_template(objective)
    if hit:
        return hit

    if complete is None:
        def complete(messages):
            return get_router().complete("planner", messages, stop=["<|im_end|>"], timeout=180)

    messages = [{"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": f"Objective: {objective}"}]
    for attempt in range(2):
        try:
            out = complete(messages)
        except Exception:
            return "", _single_task(objective)
        tasks = parse_llm_plan(out)
        if tasks:
            return "", tasks
        messages = messages + [
            {"role": "assistant", "content": out},
            {"role": "user", "content": "That was not parseable. Reply with ONLY numbered "
                                        "lines in the exact `N. [kind] goal | done: criterion` format."}]
    return "", _single_task(objective)


def replan(objective: str, state, reason: str, complete=None) -> list[Task] | None:
    """Regenerate the PENDING tail of the plan after a failure. Done tasks and their notes
    are immutable history. None when no usable replacement was produced."""
    if complete is None:
        def complete(messages):
            return get_router().complete("planner", messages, stop=["<|im_end|>"], timeout=180)

    done = "\n".join(f"- [{t.kind}] {t.goal} ({t.status})" + (f": {t.note}" if t.note else "")
                     for t in state.plan if t.status in ("done", "skipped", "failed"))
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM},
        {"role": "user", "content":
            f"Objective: {objective}\n\nProgress so far:\n{done or '(none)'}\n\n"
            f"The plan stalled: {reason}\n\n"
            f"Produce a NEW short plan for what remains (numbered lines only)."},
    ]
    try:
        tasks = parse_llm_plan(complete(messages))
    except Exception:
        return None
    if not tasks:
        return None
    start = max((t.id for t in state.plan), default=0)
    for i, t in enumerate(tasks, 1):
        t.id = start + i
    return tasks
