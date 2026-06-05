"""Magepilot agent CLI.

    # build / refresh the code index for a Magento codebase
    python -m agent.cli index --root /path/to/magento

    # ask the agent about that codebase (root defaults to the last-indexed one)
    python -m agent.cli run "Which plugin changes the product price, and where is it wired?"

    # propose the Magento commands your uncommitted changes need, and run approved ones
    python -m agent.cli suggest --root /path/to/magento
    python -m agent.cli watch   --root /path/to/magento     # same, continuously, on file change
"""
import argparse
import sys
import time

from agent import config
from agent.actions import classify, execute
from agent.codebase_index import build_index, indexed_root
from agent.db import run_query
from agent.edits import run_make, undo
from agent.react_agent import run as run_agent
from agent.suggest import git_changes, suggest


def _edit_approver(op):
    """Interactive y / n / all prompt for one file change."""
    try:
        ans = input(f"    {op['op']} {op['path']} — apply? [y]es / [n]o / [a]ll: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    return {"y": "yes", "yes": "yes", "a": "all", "all": "all"}.get(ans, "no")


def _ask(question):
    """Interactive prompt for a missing detail (e.g. vendor name)."""
    try:
        return input(question)
    except (EOFError, KeyboardInterrupt):
        return ""


def _resolve_root(arg_root: str | None) -> str:
    root = arg_root or config.DEFAULT_CODEBASE or indexed_root()
    if not root:
        sys.exit("no codebase root. Pass --root <path> or set AGENT_CODEBASE, and run `index` first.")
    return root


def _cli_approver(cmd: str, sub: str) -> str:
    """Interactive y / n / always prompt for an ASK-tier command."""
    try:
        ans = input(f"    Run `bin/magento {cmd}` ? [y]es / [n]o / [a]lways: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    return {"y": "yes", "yes": "yes", "a": "always", "always": "always"}.get(ans, "no")


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

    args = ap.parse_args(argv)

    if args.cmd == "index":
        root = args.root or config.DEFAULT_CODEBASE
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
