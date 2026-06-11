"""The orchestrator — Phase 2's state machine (docs/architecture/03):

    PLAN → [for each task: EXECUTE → OBSERVE → REFLECT] → (REPLAN | next task) → FINISH
                                                       ↘ PAUSED (checkpoint) / FAILED / BUDGET

Each task kind maps to an executor configuration:
  investigate / verify   the ReAct executor (READ tools) with the task goal + prior notes
                         (verify tasks may instead carry a deterministic `check`)
  edit                   the proven make flow: the coder role emits @@-blocks, each op is
                         previewed, approved, applied, journaled
  command                the 3-tier policy engine (auto/ask/blocked)

OBSERVE runs deterministic verification first (exit codes, files-exist checks, applied-op
counts); model judgment is never the gate. REFLECT compresses the task's output into a
≤120-token note and DROPS the scratchpad — it never crosses task boundaries. Budgets and
Ctrl-C both land in a checkpoint, so `magepilot resume <run_id>` continues exactly there.
"""
import os
import time

from magepilot import config
from magepilot.agent import compress, modes, planner, react, state as st
from magepilot.config.schema import LimitsCfg
from magepilot.edits.scaffold import run_make
from magepilot.memory import recall
from magepilot.safety.policy import classify, execute

CHECKPOINT_EVERY_STEPS = 3
MAX_REPLANS = 2
MAX_TASK_ATTEMPTS = 2


def start(objective: str, root: str, *, mode: str = "code") -> st.RunState:
    """PLAN: build a new run (template-first; LLM fallback; never fails)."""
    template, tasks = planner.plan(objective, mode=mode)
    try:
        memory_block = recall.recall_block(root, objective)
    except Exception:
        memory_block = ""              # memory must never block a run
    run = st.RunState(run_id=st.new_run_id(objective), objective=objective,
                      root=os.path.realpath(root), mode=mode, plan=tasks,
                      template=template, memory_block=memory_block,
                      started_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    st.save(run)
    st.log_event(run.run_id, "plan", template=template or "(llm)",
                 tasks=[f"[{t.kind}] {t.goal}" for t in tasks])
    return run


def resume(run_id: str) -> st.RunState:
    run = st.load(run_id)
    if run.status in ("paused", "running"):    # "running" = a crashed process; resumable
        run.status = "running"
        for t in run.plan:
            if t.status == "running":          # the task in flight when we died: re-run it
                t.status = "pending"           # (its scratchpad is preserved for replay)
                t.attempts = max(0, t.attempts - 1)
    st.log_event(run_id, "resume", status=run.status)
    return run


def run_loop(run: st.RunState, *, approver=None, asker=None, auto: bool = False,
             cancel=None, verbose: bool = True,
             limits: LimitsCfg | None = None) -> st.RunState:
    """Drive the run to a terminal state. Returns the final RunState (also checkpointed)."""
    mode = modes.get(run.mode)
    limits = modes.apply_limits(mode, limits or config.load(run.root).limits)
    deadline = time.monotonic() + limits.wall_clock_minutes * 60
    allow_always: set = set()

    def _say(msg: str) -> None:
        if verbose:
            print(msg)

    while True:
        if cancel is not None and cancel.is_set():
            return _finish(run, "paused", verbose)
        task = run.next_pending()
        if task is None:
            return _finish(run, "done", verbose)
        done_count = sum(1 for t in run.plan if t.status != "pending")
        if (run.steps_used >= limits.max_total_steps or done_count >= limits.max_tasks
                or time.monotonic() > deadline):
            return _finish(run, "budget", verbose)

        task.status = "running"
        task.attempts += 1
        run.current_task = task.id
        st.save(run)
        st.log_event(run.run_id, "task_start", task=task.id, kind=task.kind,
                     goal=task.goal, attempt=task.attempts)
        _say(f"\n\033[94m▶ task {task.id}/{len(run.plan)}\033[0m [{task.kind}] {task.goal}")

        ok, output, skip = _execute(run, task, limits=limits, approver=approver,
                                    asker=asker, auto=auto, cancel=cancel,
                                    allow_always=allow_always, verbose=verbose)

        if cancel is not None and cancel.is_set() and not ok and not skip:
            task.status = "pending"            # interrupted, not failed — retry on resume
            task.attempts -= 1
            return _finish(run, "paused", verbose)

        # REFLECT: the scratchpad/output dies here; only the note survives.
        task.note = compress.task_note(task.goal, output)
        run.executor_scratchpad = ""

        if skip:
            task.status = "skipped"
            _say(f"\033[93m→ skipped\033[0m {task.note.splitlines()[0] if task.note else ''}")
        elif ok and "NEEDS_REPLAN" not in output:
            task.status = "done"
            _say(f"\033[92m✓ done\033[0m")
        elif "NEEDS_REPLAN" in output or task.attempts >= MAX_TASK_ATTEMPTS:
            if run.replans < MAX_REPLANS:
                run.replans += 1
                reason = (output.split("NEEDS_REPLAN:", 1)[-1].strip()[:200]
                          if "NEEDS_REPLAN" in output else
                          f"task {task.id} failed {task.attempts}x: {task.note[:160]}")
                task.status = "failed"
                new_tasks = planner.replan(run.objective, run, reason)
                if new_tasks:
                    run.plan = [t for t in run.plan if t.status != "pending"] + new_tasks
                    st.log_event(run.run_id, "replan", reason=reason,
                                 tasks=[f"[{t.kind}] {t.goal}" for t in new_tasks])
                    _say(f"\033[93m↻ replanned\033[0m ({reason})")
                else:
                    return _finish(run, "failed", verbose)
            else:
                task.status = "failed"
                return _finish(run, "failed", verbose)
        else:
            task.status = "pending"            # one retry
            _say(f"\033[93m↻ retrying\033[0m task {task.id}")

        st.save(run)
        st.log_event(run.run_id, "task_end", task=task.id, status=task.status,
                     note=task.note[:200])


# ------------------------------------------------------------------ per-kind execution
def _execute(run, task, *, limits, approver, asker, auto, cancel,
             allow_always, verbose) -> tuple[bool, str, bool]:
    """Returns (ok, output_text, skip)."""
    if task.kind == "edit":
        return _execute_edit(run, task, approver=approver, asker=asker,
                             auto=auto, verbose=verbose)
    if task.kind == "command":
        return _execute_command(run, task, auto=auto, allow_always=allow_always,
                                verbose=verbose)
    if task.kind == "verify" and task.check:
        return _execute_check(run, task)
    return _execute_react(run, task, limits=limits, cancel=cancel, verbose=verbose)


def _addendum(run, task) -> str:
    parts = [f"Mode: {run.mode} — {modes.get(run.mode).prompt}"]
    if run.memory_block:
        parts.append(run.memory_block)
    parts.append(f"Current task ({task.kind}): {task.goal}")
    if task.done_when:
        parts.append(f"This task is done when: {task.done_when}")
    notes = run.notes_block()
    if notes:
        parts.append(f"Known findings from earlier tasks (trust these):\n{notes}")
    parts.append("If this task cannot succeed as stated, finish with: "
                 "Final Answer: NEEDS_REPLAN: <one-line reason>")
    return "\n".join(parts)


def _execute_react(run, task, *, limits, cancel, verbose) -> tuple[bool, str, bool]:
    remaining = max(1, limits.max_total_steps - run.steps_used)

    def on_step(scratchpad, n_steps):
        run.executor_scratchpad = scratchpad
        if n_steps % CHECKPOINT_EVERY_STEPS == 0:
            st.save(run)

    try:
        result = react.run(task.goal, run.root,
                           max_steps=min(limits.max_task_steps, remaining),
                           verbose=verbose, system_addendum=_addendum(run, task),
                           initial_scratchpad=run.executor_scratchpad,
                           on_step=on_step, cancel=cancel,
                           tools_subset=modes.get(run.mode).tools)
    except Exception as e:                                 # model server down etc.
        return False, f"executor unavailable: {e}", False
    run.steps_used += len(result["steps"])
    if result["stopped"] == "cancelled":
        run.executor_scratchpad = result.get("scratchpad", "")
        st.save(run)
        return False, "(cancelled)", False
    answer = result["answer"] or ""
    ok = result["stopped"] == "final" and bool(answer)
    if task.kind == "investigate":
        # A forced max-steps synthesis is still useful investigation output.
        ok = ok or (result["stopped"] == "max_steps" and bool(answer))
    return ok, answer, False


def _execute_edit(run, task, *, approver, asker, auto, verbose) -> tuple[bool, str, bool]:
    goal = task.goal
    notes = run.notes_block()
    if notes:
        goal += f"\n\nContext from the investigation so far (use these exact paths/names):\n{notes}"
    res = run_make(goal, run.root, approver=approver, asker=asker, auto=auto)
    run.steps_used += 1
    applied = [o["path"] for o in res["applied"]]
    skipped = [o["path"] for o in res["skipped"]]
    out = (("applied: " + ", ".join(applied)) if applied else "no changes applied")
    if skipped:
        out += "; skipped: " + ", ".join(skipped)
    for b in res.get("blocked", []):
        # the lint feedback the model needs to regenerate compliant code on retry
        out += f"; BLOCKED {b['path']}: " + "; ".join(b["reasons"])
    return bool(applied), out, False


def _execute_command(run, task, *, auto, allow_always, verbose) -> tuple[bool, str, bool]:
    run.steps_used += 1
    cmd = task.command or task.goal
    if classify(cmd) == "blocked":
        return False, f"command refused by policy: {cmd}", True     # skip, don't fail the run
    if not os.path.isfile(os.path.join(run.root, "bin", "magento")) \
            and not cmd.strip().startswith("composer"):
        return False, "no bin/magento under this root — command not applicable here", True
    if auto:
        approver = (lambda c, s: "yes")
    else:
        from magepilot.safety.approval import cli_approver as approver
    res = execute(run.root, cmd, approver=approver, allow_always=allow_always)
    if not res["ran"]:
        # Declined or unrunnable — informational, not a plan failure.
        return False, f"command not run: {res.get('reason', '?')}", True
    tail = "\n".join((res.get("output") or "").splitlines()[-12:])
    return res.get("exit") == 0, f"`{res['command']}` exit {res.get('exit')}\n{tail}", False


def _execute_check(run, task) -> tuple[bool, str, bool]:
    """Deterministic verify checks — no model involved."""
    run.steps_used += 1
    files = task.check.get("files_exist", [])
    missing = [p for p in files if not os.path.isfile(os.path.join(run.root, p))]
    if missing:
        return False, "missing files: " + ", ".join(missing), False
    return True, "verified: " + ", ".join(files), False


# ------------------------------------------------------------------ finish
def _finish(run: st.RunState, status: str, verbose: bool) -> st.RunState:
    run.status = status
    if status != "paused":
        run.answer = compress.summarize_run(run.objective, run)
        try:
            recall.extract_facts(run.root, run)    # the run's notes become project memory
        except Exception:
            pass                                   # memory must never fail a finish
    st.save(run)
    st.log_event(run.run_id, "finish", status=status)
    if verbose:
        label = {"done": "\033[92m✓ run complete\033[0m",
                 "paused": "\033[93m⏸ paused\033[0m  (resume with: magepilot resume " + run.run_id + ")",
                 "budget": "\033[93m■ budget reached\033[0m",
                 "failed": "\033[91m✗ run failed\033[0m"}[status]
        print(f"\n{label}")
        if run.answer:
            print(f"\n{run.answer}")
    return run
