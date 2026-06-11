"""MagePilot agent CLI (migrated from agent/cli.py).

    # build / refresh the code index for a Magento codebase
    python -m magepilot index --root /path/to/magento

    # ask the agent about that codebase (root defaults to the last-indexed one)
    python -m magepilot run "Which plugin changes the product price, and where is it wired?"

    # propose the Magento commands your uncommitted changes need, and run approved ones
    python -m magepilot suggest --root /path/to/magento
    python -m magepilot watch   --root /path/to/magento     # same, continuously, on file change
"""
import argparse
import os
import signal
import sys
import threading
import time

from magepilot import config
from magepilot.agent import loop, state
from magepilot.agent.react import run as run_agent
from magepilot.edits.apply import undo
from magepilot.edits.scaffold import run_make
from magepilot.index.codebase import build_index, indexed_root
from magepilot.magento.db import run_query
from magepilot.magento.suggest import git_changes, suggest
from magepilot.safety.approval import ask as _ask, cli_approver as _cli_approver, edit_approver as _edit_approver
from magepilot.safety.policy import classify, execute


def _abs_root(arg_root: str | None) -> str | None:
    """Absolutize a relative --root against the directory magepilot was launched from.

    The `magepilot` wrapper cd's into its own install dir before invoking the CLI, so a
    relative --root like "." would otherwise resolve to the install dir instead of the
    user's project. AGENT_CODEBASE preserves the original launch directory.
    """
    if arg_root and not os.path.isabs(arg_root):
        base = os.environ.get("AGENT_CODEBASE") or os.getcwd()
        return os.path.realpath(os.path.join(base, arg_root))
    return arg_root


def _resolve_root(arg_root: str | None) -> str:
    root = _abs_root(arg_root) or config.DEFAULT_CODEBASE or indexed_root()
    if not root:
        sys.exit("no codebase root. Pass --root <path> or set AGENT_CODEBASE, and run `index` first.")
    return root


def _run_suggestions(root: str, plan: bool, auto: bool, allow_always: set) -> int:
    files = git_changes(root)
    if not files:
        print("no uncommitted changes detected (git).")
        return 0
    proposals = suggest(files)
    if not proposals:
        print(f"{len(files)} changed file(s), but none require a Magento command.")
        return 0

    print(f"{len(files)} changed file(s) → {len(proposals)} suggested command(s):\n")
    for p in proposals:
        tier = classify(p["command"])
        print(f"  • bin/magento {p['command']}")
        print(f"      why: {p['reason']}")
        print(f"      for: {', '.join(p['files'][:4])}{' …' if len(p['files']) > 4 else ''}  [{tier}]")
        if plan:
            continue
        approver = (lambda c, s: "yes") if auto else _cli_approver
        result = execute(root, p["command"], approver=approver, allow_always=allow_always)
        if result["ran"]:
            print(f"      ↳ exit {result['exit']}:\n{_indent(result['output'])}")
        else:
            print(f"      ↳ skipped ({result['reason']})")
        print()
    return 0


def _indent(text: str, pad: str = "        ") -> str:
    return "\n".join(pad + ln for ln in (text or "(no output)").splitlines()[:40])


def _attach_mcp(root: str) -> None:
    """Spawn the user's configured MCP servers (config.toml [mcp_servers.*]) and add
    their tools to the registry. No servers configured → no-op; failures warn only."""
    try:
        cfg = config.load(root)
        if cfg.mcp_servers:
            from magepilot.mcp.client import attach
            from magepilot.tools import REGISTRY
            attach(REGISTRY, cfg)
    except Exception as e:
        print(f"mcp: attach failed: {e}", file=sys.stderr)


def _drive(run, *, auto: bool, quiet: bool) -> int:
    """Run the orchestrator with two-stage Ctrl-C: first → finish the current call,
    checkpoint, pause; second → hard exit (the last checkpoint is already on disk)."""
    cancel = threading.Event()

    def _sigint(signum, frame):
        if cancel.is_set():
            print("\n(hard exit — the run is checkpointed; resume with: "
                  f"magepilot resume {run.run_id})", file=sys.stderr)
            raise SystemExit(130)
        cancel.set()
        print("\n(pausing after the current step — Ctrl-C again to exit now)", file=sys.stderr)

    prev = signal.signal(signal.SIGINT, _sigint)
    try:
        run = loop.run_loop(run, approver=_edit_approver if not auto else None,
                            asker=None, auto=auto, cancel=cancel, verbose=not quiet)
    finally:
        signal.signal(signal.SIGINT, prev)
    if quiet and run.answer:
        print(run.answer)
    return {"done": 0, "paused": 0, "budget": 0}.get(run.status, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="magepilot", description="Magepilot — AI agent for Magento 2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("index", help="build/refresh the codebase index")
    p_idx.add_argument("--root", help="path to the Magento codebase")

    p_run = sub.add_parser("run", help="ask the agent about the indexed codebase")
    p_run.add_argument("task")
    p_run.add_argument("--root")
    p_run.add_argument("--max-steps", type=int, default=config.MAX_STEPS)
    p_run.add_argument("--quiet", action="store_true")

    p_sug = sub.add_parser("suggest", help="propose Magento commands for your uncommitted changes")
    p_sug.add_argument("--root")
    p_sug.add_argument("--plan", action="store_true", help="only print proposals, never execute")
    p_sug.add_argument("--auto-approve", action="store_true", help="run all proposals without asking")

    p_w = sub.add_parser("watch", help="watch the codebase and propose commands as files change")
    p_w.add_argument("--root")
    p_w.add_argument("--interval", type=float, default=2.0)
    p_w.add_argument("--plan", action="store_true")
    p_w.add_argument("--auto-approve", action="store_true")

    p_sql = sub.add_parser("sql", help="run a READ-ONLY query against the store DB (debugging)")
    p_sql.add_argument("query", help="a SELECT / SHOW / DESCRIBE / EXPLAIN statement")
    p_sql.add_argument("--root")

    p_make = sub.add_parser("make", help="create/edit files for a task, approving each change")
    p_make.add_argument("task", help="what to build, e.g. 'a Hyva theme Demo with parent Hyva/default'")
    p_make.add_argument("--root")
    p_make.add_argument("--plan", action="store_true", help="show the plan only — write nothing")
    p_make.add_argument("--auto-approve", action="store_true", help="apply every change without asking")

    p_undo = sub.add_parser("undo", help="revert the files changed by the last `make`")
    p_undo.add_argument("--root", help="override the project (defaults to where the last make ran)")

    p_do = sub.add_parser("do", help="autonomously plan and execute a multi-step objective")
    p_do.add_argument("objective", help="what to accomplish, e.g. 'create a Vendor_Faq module with an admin grid'")
    p_do.add_argument("--root")
    p_do.add_argument("--auto-approve", action="store_true",
                      help="apply file changes and ASK-tier commands without asking")
    p_do.add_argument("--mode", default="code",
                      help="ask | code | architect | debug | review | refactor | test | autonomous")
    p_do.add_argument("--quiet", action="store_true")

    p_res = sub.add_parser("resume", help="resume a paused/interrupted run")
    p_res.add_argument("run_id", nargs="?", help="run id (default: the most recent resumable run)")
    p_res.add_argument("--auto-approve", action="store_true")
    p_res.add_argument("--quiet", action="store_true")

    sub.add_parser("runs", help="list recent agent runs")

    p_g = sub.add_parser("graph", help="build/update the Magento knowledge graph")
    p_g.add_argument("--root")
    p_g.add_argument("--no-vendor", action="store_true",
                     help="skip vendor/ (faster, but core wiring answers degrade)")

    p_rev = sub.add_parser("review", help="review the uncommitted diff (advisory)")
    p_rev.add_argument("--root")

    p_tg = sub.add_parser("testgen", help="generate tests: PHPUnit, MFTF, or Playwright")
    p_tg.add_argument("target",
                      help="unit: a class FQCN · mftf/playwright: an Alpine component, "
                           "layout handle (faq_index_index), or /url/path")
    p_tg.add_argument("--kind", default="unit", choices=("unit", "mftf", "playwright"))
    p_tg.add_argument("--root")
    p_tg.add_argument("--skeleton", action="store_true",
                      help="unit only: deterministic skeleton, skip the model body fill")
    p_tg.add_argument("--auto-approve", action="store_true")

    p_mcp = sub.add_parser("mcp-serve", help="expose MagePilot's tools as an MCP stdio server")
    p_mcp.add_argument("--root")
    p_mcp.add_argument("--allow-writes", action="store_true",
                       help="also expose write tools (auto-approved — the MCP client gates them)")

    args = ap.parse_args(argv)

    if args.cmd == "index":
        root = _abs_root(args.root) or config.DEFAULT_CODEBASE
        if not root:
            sys.exit("index needs --root <path to Magento codebase>")
        build_index(root)
        return 0

    if args.cmd == "run":
        root = _resolve_root(args.root)
        result = run_agent(args.task, root, max_steps=args.max_steps, verbose=not args.quiet)
        if args.quiet:
            print(result["answer"])
        print(f"\n[{len(result['steps'])} tool step(s); stopped: {result['stopped']}]", file=sys.stderr)
        return 0

    if args.cmd == "suggest":
        root = _resolve_root(args.root)
        return _run_suggestions(root, plan=args.plan, auto=args.auto_approve, allow_always=set())

    if args.cmd == "watch":
        root = _resolve_root(args.root)
        allow_always: set = set()
        print(f"watching {root} (every {args.interval}s) — Ctrl-C to stop\n")
        seen = set(git_changes(root))
        try:
            while True:
                time.sleep(args.interval)
                current = set(git_changes(root))
                if current != seen and (current - seen):
                    print("— change detected —")
                    _run_suggestions(root, plan=args.plan, auto=args.auto_approve, allow_always=allow_always)
                seen = current
        except KeyboardInterrupt:
            print("\nstopped.")
        return 0

    if args.cmd == "make":
        root = _resolve_root(args.root)
        run_make(args.task, root, approver=_edit_approver, asker=_ask,
                 auto=args.auto_approve, plan_only=args.plan)
        return 0

    if args.cmd == "undo":
        undo(args.root)          # root optional — defaults to where the last make ran
        return 0

    if args.cmd == "do":
        root = _resolve_root(args.root)
        _attach_mcp(root)
        run = loop.start(args.objective, root, mode=args.mode)
        print(f"run {run.run_id}" + (f"  (template: {run.template})" if run.template else ""))
        return _drive(run, auto=args.auto_approve, quiet=args.quiet)

    if args.cmd == "resume":
        rid = args.run_id
        if not rid:
            resumable = [r for r in state.list_runs() if r["status"] in ("paused", "running")]
            if not resumable:
                print("no resumable runs.", file=sys.stderr)
                return 1
            rid = resumable[0]["run_id"]
        run = loop.resume(rid)
        _attach_mcp(run.root)
        if run.status not in ("running",):
            print(f"run {rid} is '{run.status}' — nothing to resume.", file=sys.stderr)
            return 1
        print(f"resuming {rid}: {run.objective}")
        return _drive(run, auto=args.auto_approve, quiet=args.quiet)

    if args.cmd == "runs":
        rows = state.list_runs()
        if not rows:
            print("no runs yet — start one with: magepilot do \"<objective>\"")
            return 0
        for r in rows[:20]:
            print(f"{r['run_id']:<46} {r['status']:<8} {r['objective'][:60]}")
        return 0

    if args.cmd == "graph":
        from magepilot.graph.build import build as build_graph
        root = _resolve_root(args.root)
        build_graph(root, vendor=not args.no_vendor)
        return 0

    if args.cmd == "mcp-serve":
        from magepilot.mcp.server import serve as mcp_serve
        root = _resolve_root(args.root)
        mcp_serve(root, allow_writes=args.allow_writes)
        return 0

    if args.cmd == "testgen":
        root = _resolve_root(args.root)
        if args.kind == "unit":
            from magepilot.testgen import write_test
            res = write_test(root, args.target, approver=_edit_approver,
                             auto=args.auto_approve, fill=not args.skeleton)
            if res["written"]:
                print(f"\n\u2713 wrote {res['path']}  (run it with: vendor/bin/phpunit {res['path']})")
            return 0 if res["written"] else 1
        from magepilot.testgen import mftf as tg_mftf, playwright as tg_pw
        gen = tg_mftf if args.kind == "mftf" else tg_pw
        try:
            res = gen.write(root, args.target, approver=_edit_approver,
                            auto=args.auto_approve)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0 if res["written"] else 1

    if args.cmd == "review":
        from magepilot.review.reviewer import review_uncommitted
        root = _resolve_root(args.root)
        issues = review_uncommitted(root)
        if issues is None:
            print("nothing to review (no uncommitted changes, or the model is unreachable).")
            return 0
        if not issues:
            print("no issues found.")
            return 0
        for i in issues:
            print(f"  {i.severity.upper():<6} {i.file}:{i.line} [{i.category}] {i.text}")
        return 0

    if args.cmd == "sql":
        root = _resolve_root(args.root)
        result = run_query(root, args.query)
        if result["ok"]:
            print(result["output"])
            return 0
        print(f"error: {result['error']}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
