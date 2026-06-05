"""Magepilot interactive shell — a Claude-Code-style REPL.

Run it with no arguments (`./magepilot`) and you get a prompt: type a question for a
grounded Magento answer, or use /slash commands for actions (install, serve, index, sql, …).

    magepilot> how do I add an extra charge to a product's price with a plugin?
    magepilot> /index ~/PhpstormProjects/my-store
    magepilot> /code where is the cart discount applied?
    magepilot> /sql SELECT sku FROM catalog_product_entity LIMIT 5
    magepilot> /exit
"""
import atexit
import os
import re
import signal
import socket
import subprocess
import sys

try:
    import readline  # noqa: F401 — enables history/line-editing at the prompt
except ImportError:
    pass

from agent.codebase_index import indexed_root, is_indexed

# Plain input that clearly asks to scaffold/modify files → route to the (approval-gated) make flow.
_BUILD_RE = re.compile(
    r"^\s*(create|add|generate|scaffold|build|set ?up|new|implement)\b.*"
    r"\b(theme|module|plugin|controller|observer|block|view ?model|template|command|model|"
    r"repository|cron|patch|widget|component|file|directory|folder|graphql|resolver|route|menu|"
    r"system\.xml|di\.xml|db_schema|webapi|email|class|interface)\b", re.I)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAGEPILOT = os.path.join(ROOT, "magepilot")
PY = sys.executable

B, G, Y, R, X = "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[0m"

HELP = f"""{B}Magepilot{X} — a local AI assistant for Magento 2 & Hyvä.

  {G}First time{X}
    1. /install                 set up the environment + knowledge base (one-off)
    2. /serve                   start the model + RAG servers (first run downloads the model)
    3. just type a question, e.g.
         add an extra charge to a product's price with a plugin
         create an observer for catalog_product_save_after
         apply a discount to the cart with a total collector
         add a free gift product when an item is added to the cart
    4. /index <path>            point it at YOUR store, then ask about real code with /code

  {G}Ask{X}
    <just type>                 grounded Magento answer (knowledge base + fine-tuned model)
    /code <task>                the agent inspects your indexed codebase to answer
                                e.g. /code which plugin changes the product price?
    /sql <SELECT …>             read-only query on the store DB
                                e.g. /sql SELECT sku FROM catalog_product_entity LIMIT 5

  {G}Build / change files (asks before EVERY change){X}
    /make <task>                create/edit files for a task — you approve each change (y/n/all)
                                e.g. /make a Hyva theme Demo with parent Hyva/default
    /undo                       revert the files changed by the last /make
    (typing "create a … theme/module/plugin/…" routes here automatically)

  {G}Codebase{X}
    /index <path>               index a Magento codebase (sets it as current)
    /use <path>                 set the current codebase (without re-indexing)
    /suggest                    propose & run the Magento commands your changes need (you approve)
    /watch                      keep proposing commands as you edit files

  {G}Servers{X}
    /install                    set up env, deps, knowledge base
    /serve   /stop   /status    start / stop / check the model + RAG servers

  {G}Shell{X}
    /help                       this help
    /exit · exit · Ctrl-D · Ctrl-C · Ctrl-Z   quit — stops the model + RAG servers and any
                                stuck magepilot process (keep servers up: MAGEPILOT_KEEP_SERVE=1)
"""


# Command catalog — drives the `/` menu and Tab-completion.
COMMANDS = [
    ("/make", "<task>", "create/edit files for a task (you approve each change)"),
    ("/undo", "", "revert the files changed by the last /make"),
    ("/index", "[path]", "index this project so /code can search it"),
    ("/code", "<task>", "answer from your real code (the agent inspects it)"),
    ("/sql", "<SELECT…>", "read-only query against the store DB"),
    ("/suggest", "", "propose & run the Magento commands your changes need"),
    ("/watch", "", "keep proposing commands as you edit files"),
    ("/use", "<path>", "switch to a different project"),
    ("/serve", "", "start the model + RAG servers"),
    ("/stop", "", "stop the servers"),
    ("/status", "", "show what's running"),
    ("/install", "", "set up env, deps, knowledge base"),
    ("/help", "", "full help"),
    ("/exit", "", "quit & stop the servers (also: exit, Ctrl-D)"),
]


def _menu() -> None:
    print(f"{B}/commands{X}  (or just type a question)")
    for name, args, desc in COMMANDS:
        print(f"  {G}{name:<9}{X} {Y}{args:<11}{X} {desc}")


def _completer(text, state):
    """Readline fallback: Tab-complete slash commands (press Tab after `/`)."""
    if not text.startswith("/"):
        return None
    matches = [name + " " for name, _, _ in COMMANDS if name.startswith(text)]
    return matches[state] if state < len(matches) else None


def _slash_completions(text):
    """Yield (insert_text, display, description) for the live `/` menu, given the
    text before the cursor. Empty unless we're typing the leading command token."""
    if not text.startswith("/") or " " in text:        # only the leading command token
        return
    for name, args, desc in COMMANDS:
        if name.startswith(text.lower()):
            yield name, f"{name} {args}".rstrip(), desc


def _make_pt_reader():
    """A line reader with a live slash-command menu (like Claude Code): the moment you
    type `/` the command list drops down — no Enter, no Tab needed. Backed by
    prompt_toolkit; returns None if it isn't installed (or stdin isn't a terminal) so the
    caller falls back to readline + plain input()."""
    if not sys.stdin.isatty():
        return None
    try:
        from prompt_toolkit import ANSI, PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
    except ImportError:
        return None

    class _SlashMenu(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            for insert, display, desc in _slash_completions(text):
                yield Completion(insert, start_position=-len(text),
                                 display=display, display_meta=desc)

    # prompt_toolkit grabs Ctrl-Z as a key (no OS SIGTSTP fires while the prompt is up),
    # so bind it here to a clean quit → exit handler stops the servers. (Ctrl-C already
    # raises KeyboardInterrupt and Ctrl-D EOFError, both handled by the loop.)
    kb = KeyBindings()

    @kb.add("c-z")
    def _(event):
        event.app.exit(exception=EOFError)

    session = PromptSession(completer=_SlashMenu(), complete_while_typing=True,
                            history=InMemoryHistory(), key_bindings=kb)
    return lambda prompt: session.prompt(ANSI(prompt))


def sh(args) -> int:
    """Run a subcommand from the repo root, streaming its output."""
    return subprocess.run(args, cwd=ROOT).returncode


def _server_running() -> bool:
    """True if the model (:8080) or RAG (:8090) server is listening locally."""
    for port in (8080, 8090):
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
    return False


def _stop_servers_on_exit() -> None:
    """Stop the model + RAG servers when the REPL ends — on /exit, Ctrl-D, Ctrl-C, or a
    SIGTERM/SIGHUP (closed terminal) — so they don't linger in the background eating RAM
    (and disk, via swap). Opt out with MAGEPILOT_KEEP_SERVE=1 to keep a server up for an IDE."""
    if getattr(_stop_servers_on_exit, "_done", False):
        return
    _stop_servers_on_exit._done = True
    if os.environ.get("MAGEPILOT_KEEP_SERVE") or not _server_running():
        return
    print(f"\n{Y}stopping servers…{X}")
    subprocess.run([MAGEPILOT, "stop"], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"{G}✓{X} servers stopped")


def _install_exit_cleanup() -> None:
    """Make a closed terminal / kill behave like a clean exit, then stop the servers."""
    atexit.register(_stop_servers_on_exit)

    def _bye(signum, frame):
        raise SystemExit(0)          # unwinds to main()'s return → atexit runs

    # SIGTERM/SIGHUP: kill or closed terminal.  SIGTSTP: Ctrl-Z outside the prompt (readline
    # fallback, or while a command is running) — we quit & clean up instead of suspending.
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGTSTP):
        try:
            signal.signal(sig, _bye)
        except (ValueError, OSError):
            pass                     # not on the main thread / unsupported — atexit still covers exit


def main() -> int:
    state = {"root": os.environ.get("AGENT_CODEBASE") or indexed_root()}
    _install_exit_cleanup()                        # stop the servers when this session ends
    read = _make_pt_reader()                       # live `/` menu (Claude-Code style)
    if read is None and "readline" in sys.modules:  # fallback: press `/` then Tab
        import readline
        readline.set_completer(_completer)
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")
    hint = f"Type {G}/{X} for commands" if read else f"Type {G}/{X} then Tab for commands"
    print(f"{B}Magepilot{X} — Magento 2 AI assistant.  {hint}, a question to ask, /exit to quit.")
    if state["root"]:
        print(f"{Y}codebase:{X} {state['root']}")
        if not is_indexed(state["root"]):
            print(f"{Y}(this project isn't indexed yet — run /index to enable /code){X}")

    while True:
        prompt = f"{B}magepilot>{X} "
        try:
            line = (read(prompt) if read else input(prompt)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q", ":q", ":quit"):   # bare exit, not just /exit
            return 0

        if not line.startswith("/"):
            if _BUILD_RE.search(line):                 # "create a Hyva theme …" → make files (you approve each)
                if _need_root(state):
                    print(f"{Y}(creating files — you'll approve each change; Ctrl-C to cancel){X}")
                    sh([PY, "-m", "agent.cli", "make", "--root", state["root"], line])
            else:
                sh([PY, "rag/ask.py", "--quiet", line])  # a question → grounded answer (answer only)
            continue

        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if not cmd:                      # bare "/" → show all commands (menu)
            _menu()
            continue
        if cmd in ("exit", "quit", "q"):
            return 0
        elif cmd in ("help", "h", "?"):
            print(HELP)
        elif cmd == "install":
            sh([MAGEPILOT, "install"])
        elif cmd in ("serve", "start"):
            sh([MAGEPILOT, "serve"])
        elif cmd == "stop":
            sh([MAGEPILOT, "stop"])
        elif cmd == "status":
            sh([MAGEPILOT, "status"])
        elif cmd == "index":
            path = os.path.abspath(os.path.expanduser(arg)) if arg else state["root"]
            if not path:
                print(f"{R}usage:{X} /index <path-to-magento>")
            elif sh([PY, "-m", "agent.cli", "index", "--root", path]) == 0:
                state["root"] = path
        elif cmd in ("use", "cd", "root"):
            if not arg:
                print(f"current codebase: {state['root'] or '(none)'}")
            else:
                state["root"] = os.path.abspath(os.path.expanduser(arg))
                print(f"{Y}codebase:{X} {state['root']}")
        elif cmd in ("code", "agent", "run"):
            _need_root(state) and sh([PY, "-m", "agent.cli", "run", "--root", state["root"], arg])
        elif cmd in ("make", "build", "scaffold", "edit"):
            _need_root(state) and sh([PY, "-m", "agent.cli", "make", "--root", state["root"], arg])
        elif cmd in ("undo", "revert"):
            sh([PY, "-m", "agent.cli", "undo"])   # reverts the last make (uses its recorded project)
        elif cmd == "sql":
            _need_root(state) and sh([PY, "-m", "agent.cli", "sql", "--root", state["root"], arg])
        elif cmd == "suggest":
            _need_root(state) and sh([PY, "-m", "agent.cli", "suggest", "--root", state["root"]])
        elif cmd == "watch":
            _need_root(state) and sh([PY, "-m", "agent.cli", "watch", "--root", state["root"]])
        else:
            print(f"{R}unknown command:{X} /{cmd}   —  /help")


def _need_root(state) -> bool:
    if state["root"]:
        return True
    print(f"{R}no codebase set{X} — run /index <path> or /use <path> first")
    return False


if __name__ == "__main__":
    raise SystemExit(main())