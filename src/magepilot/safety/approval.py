"""Interactive approval frontends (migrated from agent/cli.py). The policy engine takes
any callable with these shapes, so non-interactive contexts pass auto-deny/auto-approve
callbacks instead."""


def edit_approver(op: dict) -> str:
    """Interactive y / n / all prompt for one file change."""
    try:
        ans = input(f"    {op['op']} {op['path']} — apply? [y]es / [n]o / [a]ll: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    return {"y": "yes", "yes": "yes", "a": "all", "all": "all"}.get(ans, "no")


def cli_approver(cmd: str, sub: str) -> str:
    """Interactive y / n / always prompt for an ASK-tier command."""
    try:
        ans = input(f"    Run `bin/magento {cmd}` ? [y]es / [n]o / [a]lways: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "no"
    return {"y": "yes", "yes": "yes", "a": "always", "always": "always"}.get(ans, "no")


def ask(question: str) -> str:
    """Interactive prompt for a missing detail (e.g. vendor name)."""
    try:
        return input(question)
    except (EOFError, KeyboardInterrupt):
        return ""


def deny_all(*_args, **_kwargs) -> str:
    """Safe default for non-interactive contexts."""
    return "no"
