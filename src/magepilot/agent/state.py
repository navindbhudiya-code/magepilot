"""Run state for the orchestrator: the task queue, budgets used, and checkpointing.

A run lives at <RUNS_DIR>/<run_id>/ with two files:
  checkpoint.json   the full RunState, rewritten ATOMICALLY (tmp + rename) after every
                    task transition and every few executor steps — `magepilot resume`
                    rehydrates from it, including a mid-task ReAct scratchpad
  events.jsonl      append-only audit trail (plan, task_start, task_done, replan, finish)
"""
import dataclasses
import json
import os
import re
import time
from dataclasses import dataclass, field

from magepilot import config

CHECKPOINT_VERSION = 1

# The closed set of task kinds — each maps to an executor configuration in loop.py.
TASK_KINDS = ("investigate", "edit", "command", "verify")


@dataclass
class Task:
    id: int
    kind: str                      # investigate | edit | command | verify
    goal: str
    done_when: str = ""
    status: str = "pending"        # pending | running | done | failed | skipped
    attempts: int = 0
    note: str = ""                 # the ≤120-token reflection note (survives the run)
    command: str = ""              # command tasks: the exact bin/magento/composer command
    check: dict = field(default_factory=dict)  # verify tasks: deterministic check,
                                               # e.g. {"files_exist": ["app/code/.../registration.php"]}


@dataclass
class RunState:
    run_id: str
    objective: str
    root: str
    mode: str = "code"
    status: str = "running"        # running | done | failed | paused | budget
    plan: list[Task] = field(default_factory=list)
    template: str = ""             # which planner template produced the plan ("" = LLM/fallback)
    current_task: int = 0
    executor_scratchpad: str = ""  # mid-task ReAct scratchpad (for resume)
    steps_used: int = 0
    replans: int = 0
    answer: str = ""               # the final summary shown to the user
    started_at: str = ""
    checkpointed_at: str = ""
    version: int = CHECKPOINT_VERSION

    # ------------------------------------------------------------------ queries
    def next_pending(self) -> Task | None:
        return next((t for t in self.plan if t.status == "pending"), None)

    def notes_block(self) -> str:
        """Accumulated task notes, injected into later executors' prompts."""
        lines = [f"- [{t.kind}] {t.goal}: {t.note}" for t in self.plan if t.note]
        return "\n".join(lines)


# ------------------------------------------------------------------ persistence
def new_run_id(objective: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")[:32] or "run"
    return time.strftime("%Y%m%d-%H%M%S") + "-" + slug


def run_dir(run_id: str) -> str:
    return os.path.join(config.RUNS_DIR, run_id)


def save(state: RunState) -> None:
    """Atomic checkpoint: write to a temp file in the same dir, then rename over."""
    state.checkpointed_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    d = run_dir(state.run_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "checkpoint.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(state), f, indent=1)
    os.replace(tmp, path)


def load(run_id: str) -> RunState:
    with open(os.path.join(run_dir(run_id), "checkpoint.json"), encoding="utf-8") as f:
        data = json.load(f)
    data["plan"] = [Task(**t) for t in data.get("plan", [])]
    return RunState(**data)


def list_runs() -> list[dict]:
    """[{run_id, status, objective, checkpointed_at}] — newest first."""
    out = []
    try:
        ids = sorted(os.listdir(config.RUNS_DIR), reverse=True)
    except OSError:
        return out
    for rid in ids:
        try:
            with open(os.path.join(config.RUNS_DIR, rid, "checkpoint.json"), encoding="utf-8") as f:
                d = json.load(f)
            out.append({"run_id": rid, "status": d.get("status", "?"),
                        "objective": d.get("objective", ""),
                        "checkpointed_at": d.get("checkpointed_at", "")})
        except (OSError, json.JSONDecodeError):
            continue
    return out


def log_event(run_id: str, event: str, **fields) -> None:
    """Append one line to the run's events.jsonl (best-effort, never raises)."""
    try:
        d = run_dir(run_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "event": event, **fields}) + "\n")
    except OSError:
        pass
