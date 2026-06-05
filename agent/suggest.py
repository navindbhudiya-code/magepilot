"""Map uncommitted code changes (from git) to the Magento commands they require.

The mapping is RULE-BASED and deterministic — the model never invents a command. Each rule
matches changed file paths and proposes the canonical (dev-mode) command(s) plus a reason.
"""
import subprocess

# (path predicate, [commands], reason). Order of RULES doesn't matter — output is ranked below.
RULES = [
    (lambda p: p.endswith("db_schema.xml"),
     ["setup:db-declaration:generate-whitelist", "setup:upgrade"],
     "declarative schema changed — regenerate the whitelist and apply it"),
    (lambda p: p.endswith("module.xml") or p.endswith("registration.php"),
     ["setup:upgrade"],
     "module declaration changed — Magento must (re)register it"),
    (lambda p: p.endswith("di.xml"),
     ["cache:clean config"],
     "DI config changed (dev: clean config cache; production needs setup:di:compile)"),
    (lambda p: p.endswith("events.xml") or "/Observer/" in p,
     ["cache:clean config"],
     "observer/event wiring changed"),
    (lambda p: p.endswith(".phtml") or "/templates/" in p or "/layout/" in p
     or p.endswith("_layout.xml") or p.endswith("default.xml"),
     ["cache:clean block_html layout full_page"],
     "templates/layout changed — clear the rendered caches"),
    (lambda p: p.endswith(".csv") and "/i18n/" in p,
     ["cache:clean"],
     "translations changed"),
    (lambda p: p.endswith(".graphqls") or any(p.endswith(x) for x in (
        "system.xml", "config.xml", "acl.xml", "crontab.xml", "webapi.xml",
        "email_templates.xml", "fieldset.xml", "extension_attributes.xml",
        "routes.xml", "menu.xml", "widget.xml", "page_types.xml", "sections.xml",
        "view.xml", "communication.xml", "queue_consumer.xml", "queue_topology.xml")),
     ["cache:clean config"],
     "module configuration changed"),
    (lambda p: p.endswith("composer.json") or p.endswith("composer.lock"),
     ["composer dump-autoload"],
     "composer metadata changed — regenerate the autoloader"),
    (lambda p: p.endswith("indexer.xml") or p.endswith("mview.xml"),
     ["setup:upgrade", "indexer:reindex"],
     "indexer/mview definition changed — re-declare and reindex"),
    (lambda p: "/Setup/" in p and p.endswith(".php"),
     ["setup:upgrade"],
     "data/schema setup patch changed — apply it"),
    (lambda p: p.endswith("requirejs-config.js") or "/web/" in p
     or p.endswith(".less") or p.endswith("/requirejs-config.js"),
     ["setup:static-content:deploy -f"],
     "JS/CSS/RequireJS changed — redeploy static assets "
     "(dev: usually just clear pub/static + var/view_preprocessed and cache:clean)"),
    (lambda p: p.endswith(".xsd"),
     ["cache:clean config"],
     "XML schema (.xsd) changed — clear config cache"),
]

# Commands earlier in this list run first; anything else keeps a stable order after them.
# Values are the BASE token (first word) of the command.
_ORDER = [
    "composer",
    "setup:db-declaration:generate-whitelist",
    "setup:upgrade",
    "setup:di:compile",
    "setup:static-content:deploy",
    "indexer:reindex",
    "cache:clean",
    "cache:flush",
]


def parse_porcelain(text: str) -> list[str]:
    """Extract changed file paths from `git status --porcelain` output (handles renames)."""
    files = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:          # rename: "old -> new"
            path = path.split(" -> ")[-1]
        files.append(path)
    return files


def git_changes(root: str) -> list[str]:
    """Changed (uncommitted) file paths in the git repo at `root`, or [] if not a repo."""
    try:
        out = subprocess.run(
            # --untracked-files=all expands new files inside untracked dirs (git otherwise
            # collapses them to '?? some/dir/'), so brand-new module files are detected too.
            ["git", "-C", root, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if out.returncode != 0:
        return []
    return parse_porcelain(out.stdout)


def suggest(files: list[str]) -> list[dict]:
    """Map changed files to ordered, de-duplicated command proposals.

    Returns [{command, reason, files:[...]}], ordered so prerequisites run first.
    """
    proposals: dict[str, dict] = {}
    for f in files:
        norm = f.replace("\\", "/")
        for predicate, commands, reason in RULES:
            if predicate(norm):
                for cmd in commands:
                    entry = proposals.setdefault(cmd, {"reason": reason, "files": set()})
                    entry["files"].add(f)

    def rank(cmd: str) -> int:
        base = cmd.split(" ", 1)[0]
        return _ORDER.index(base) if base in _ORDER else len(_ORDER)

    return [
        {"command": cmd, "reason": proposals[cmd]["reason"], "files": sorted(proposals[cmd]["files"])}
        for cmd in sorted(proposals, key=lambda c: (rank(c), c))
    ]
