"""`python -m magepilot` — no arguments starts the interactive REPL; any argument
is forwarded to the CLI (index / run / make / suggest / watch / sql / undo)."""
import sys


def main() -> int:
    if len(sys.argv) <= 1:
        from magepilot.ui.repl import main as repl_main
        return repl_main()
    from magepilot.ui.cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
