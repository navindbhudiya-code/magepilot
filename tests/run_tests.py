"""Deterministic tests for MagePilot — NO model required.

Covers the parts whose correctness must not depend on the LLM: the sandbox, every tool,
the CLI whitelist, the ReAct parser, code indexing + semantic search on a fixture module,
and (new in v2) the tool registry/permission gate, the config loader, and the model router.

    python tests/run_tests.py
"""
import os
import shutil
import tempfile
import traceback

from magepilot import config

# Redirect the code index to a throwaway dir BEFORE the collection is opened.
_TMP = tempfile.mkdtemp(prefix="magepilot-test-")
config.CODE_CHROMA_PATH = os.path.join(_TMP, "code_index")
config.ROOT_MARKER = os.path.join(config.CODE_CHROMA_PATH, "root.txt")
config.UNDO_FILE = os.path.join(_TMP, "last_make.json")
config.RUNS_DIR = os.path.join(_TMP, "runs")

from magepilot import edits, tools                                    # noqa: E402
from magepilot.agent import compress, loop, planner                   # noqa: E402
from magepilot.agent import react as react_agent                      # noqa: E402
from magepilot.agent import state as run_state                       # noqa: E402
from magepilot.config import loader as config_loader                  # noqa: E402
from magepilot.config.schema import Config, LimitsCfg, ProviderCfg    # noqa: E402
from magepilot.edits import scaffold                                  # noqa: E402
from magepilot.index import codebase as codebase_index                # noqa: E402
from magepilot.llm import providers as llm_providers                  # noqa: E402
from magepilot.llm.router import Router, RouterError                  # noqa: E402
from magepilot.magento import db, suggest                             # noqa: E402
from magepilot.safety import policy as actions                        # noqa: E402
from magepilot.tools.base import Param, RiskLevel, Tool, ToolContext  # noqa: E402
from magepilot.tools.registry import ToolRegistry                     # noqa: E402

codebase_index._collections.clear()

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture")
ROOT = os.path.join(FIXTURE, "Vendor", "Faq")

_results = []


def test(name):
    def deco(fn):
        _results.append((name, fn))
        return fn
    return deco


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expected truthy")


def assert_in(needle, hay, msg=""):
    if needle not in hay:
        raise AssertionError(msg or f"expected '{needle}' in: {hay[:200]!r}")


def assert_raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} to be raised")


# ------------------------------------------------------------------ sandbox + file tools
@test("sandbox blocks path traversal outside the root")
def _():
    assert_raises(tools.ToolError, tools.read_file, ROOT, "../../../../etc/passwd")
    assert_raises(tools.ToolError, tools.read_file, ROOT, "/etc/passwd")


@test("read_file returns content and respects a line range")
def _():
    out = tools.read_file(ROOT, "Model/FaqRepository.php")
    assert_in("NoSuchEntityException", out)
    ranged = tools.read_file(ROOT, "Model/FaqRepository.php", 1, 3)
    assert_in("declare(strict_types=1)", ranged)
    assert_true("getById" not in ranged, "line range should exclude later lines")


@test("list_dir lists module subdirectories")
def _():
    out = tools.list_dir(ROOT, ".")
    assert_in("Model/", out)
    assert_in("etc/", out)
    assert_in("registration.php", out)


@test("find_files matches by glob and substring")
def _():
    assert_in("etc/di.xml", tools.find_files(ROOT, "di.xml"))
    assert_in("FaqRepository.php", tools.find_files(ROOT, "*Repository.php"))
    assert_in("no files match", tools.find_files(ROOT, "*.tsx"))


@test("grep finds an exact symbol with file:line")
def _():
    out = tools.grep(ROOT, "NoSuchEntityException")
    assert_in("FaqRepository.php", out)
    assert_in("class AddSurcharge", tools.grep(ROOT, "class AddSurcharge"))


# ------------------------------------------------------------------ cli safety
@test("magento_cli rejects non-whitelisted commands")
def _():
    assert_raises(tools.ToolError, tools.magento_cli, ROOT, "setup:upgrade")
    assert_raises(tools.ToolError, tools.magento_cli, ROOT, "cache:flush")


@test("magento_cli rejects shell metacharacters")
def _():
    assert_raises(tools.ToolError, tools.magento_cli, ROOT, "module:status; rm -rf /")
    assert_raises(tools.ToolError, tools.magento_cli, ROOT, "module:status && curl evil")


@test("magento_cli allows a whitelisted command but reports no Magento root")
def _():
    out = tools.magento_cli(ROOT, "module:status")
    assert_in("no Magento root configured", out)


# ------------------------------------------------------------------ dispatch + arg parsing
@test("run_tool dispatches and reports unknown tools / bad args safely")
def _():
    assert_in("unknown tool", tools.run_tool(ROOT, "nope", "x"))
    assert_in("NoSuchEntityException", tools.run_tool(ROOT, "grep", "NoSuchEntityException"))
    # JSON object input for read_file
    out = tools.run_tool(ROOT, "read_file", '{"path": "Plugin/AddSurcharge.php", "start": 1, "end": 5}')
    assert_in("AddSurcharge", out)


@test("_parse_args accepts JSON objects and plain strings")
def _():
    assert_true(tools._parse_args('{"path":"a","start":2}', "path") == {"path": "a", "start": 2})
    assert_true(tools._parse_args('"hello"', "query") == {"query": "hello"})
    assert_true(tools._parse_args("plain text", "pattern") == {"pattern": "plain text"})
    assert_true(tools._parse_args("", "query") == {})


# ------------------------------------------------------------------ ReAct parser
@test("ReAct parser extracts Action / Action Input")
def _():
    txt = "Thought: look it up\nAction: grep\nAction Input: NoSuchEntityException\n"
    m = react_agent._ACTION_RE.search(txt)
    assert_true(m is not None, "action regex should match")
    assert_true(m.group(1).strip() == "grep")
    assert_in("NoSuchEntityException", m.group(2))


@test("ReAct parser extracts a Final Answer")
def _():
    m = react_agent._FINAL_RE.search("Thought: done\nFinal Answer: it is in FaqRepository.php:20")
    assert_true(m is not None)
    assert_in("FaqRepository.php", m.group(1))


@test("_first_arg isolates a JSON object or the first line")
def _():
    assert_true(react_agent._first_arg('{"path":"a"}\nextra junk') == '{"path":"a"}')
    assert_true(react_agent._first_arg("di.xml\nObservation: ...") == "di.xml")


# ------------------------------------------------------------------ indexing + semantic search
@test("build_index indexes the fixture and search_code finds the right file")
def _():
    n = codebase_index.build_index(ROOT, verbose=False)
    assert_true(n > 0, "index should contain chunks")
    out = tools.search_code(ROOT, "repository method that throws a not-found exception by id", k=5)
    assert_in("FaqRepository.php", out)
    out2 = tools.search_code(ROOT, "plugin that adds an extra charge to the product price", k=5)
    assert_in("AddSurcharge.php", out2)


# ------------------------------------------------------------------ command actions (safety)
@test("classify tiers: auto / ask / blocked")
def _():
    assert_true(actions.classify("module:status") == "auto")
    assert_true(actions.classify("cache:status") == "auto")
    assert_true(actions.classify("setup:upgrade") == "ask")
    assert_true(actions.classify("cache:clean config") == "ask")
    assert_true(actions.classify("setup:uninstall") == "blocked", "non-whitelisted must block")
    assert_true(actions.classify("setup:upgrade; rm -rf /") == "blocked", "metachars must block")
    assert_true(actions.classify("rm -rf /") == "blocked")


@test("execute refuses blocked commands without running")
def _():
    r = actions.execute(ROOT, "rm -rf /")
    assert_true(r["ran"] is False and r["tier"] == "blocked")


@test("execute requires approval for state-changing commands")
def _():
    calls = []
    r = actions.execute(ROOT, "setup:upgrade", approver=lambda c, s: calls.append(c) or "no")
    assert_true(calls == ["setup:upgrade"], "approver must be consulted")
    assert_true(r["ran"] is False and r["reason"] == "declined by user")


@test("execute never prompts for auto (read-only) commands")
def _():
    called = []
    actions.execute(ROOT, "module:status", approver=lambda c, s: called.append(c) or "no")
    assert_true(called == [], "auto-tier commands must not prompt")


@test("'always' approval is remembered for the session")
def _():
    allow, n = set(), [0]
    def always(cmd, sub):
        n[0] += 1
        return "always"
    actions.execute(ROOT, "cache:clean", approver=always, allow_always=allow)
    actions.execute(ROOT, "cache:clean", approver=always, allow_always=allow)
    assert_true(n[0] == 1, "approver should run once, then auto-approve")
    assert_true("cache:clean" in allow)


# ------------------------------------------------------------------ change -> command mapping
@test("suggest maps db_schema.xml to whitelist + upgrade, in order")
def _():
    cmds = [p["command"] for p in suggest.suggest(["app/code/Vendor/Mod/etc/db_schema.xml"])]
    assert_true("setup:db-declaration:generate-whitelist" in cmds and "setup:upgrade" in cmds)
    assert_true(cmds.index("setup:db-declaration:generate-whitelist") < cmds.index("setup:upgrade"),
                "whitelist must be proposed before upgrade")


@test("suggest maps di.xml and templates to the right cache cleans")
def _():
    di = [p["command"] for p in suggest.suggest(["app/code/V/M/etc/di.xml"])]
    assert_true(any(c.startswith("cache:clean") for c in di))
    tpl = [p["command"] for p in suggest.suggest(["app/code/V/M/view/frontend/templates/x.phtml"])]
    assert_true(any("block_html" in c for c in tpl))


@test("suggest returns nothing for irrelevant changes")
def _():
    assert_true(suggest.suggest(["README.md", "LICENSE", "docs/guide.txt", "notes.txt"]) == [])


@test("parse_porcelain extracts paths and handles renames")
def _():
    files = suggest.parse_porcelain(
        " M app/code/V/M/etc/di.xml\n?? new/File.php\nR  old.php -> app/code/V/M/Renamed.php\n")
    joined = "\n".join(files)
    assert_in("app/code/V/M/etc/di.xml", joined)
    assert_in("app/code/V/M/Renamed.php", joined)


@test("suggest covers composer, indexer, setup patches, and JS/static")
def _():
    assert_true(any(p["command"].startswith("composer") for p in suggest.suggest(["composer.json"])))
    idx = [p["command"] for p in suggest.suggest(["app/code/V/M/etc/indexer.xml"])]
    assert_true("indexer:reindex" in idx and "setup:upgrade" in idx)
    patch = [p["command"] for p in suggest.suggest(["app/code/V/M/Setup/Patch/Data/AddX.php"])]
    assert_true("setup:upgrade" in patch)
    js = [p["command"] for p in suggest.suggest(["app/code/V/M/view/frontend/web/js/x.js"])]
    assert_true(any("static-content" in c for c in js))


@test("composer commands are tier 'ask'; unknown composer subcommands are blocked")
def _():
    assert_true(actions.classify("composer dump-autoload") == "ask")
    assert_true(actions.classify("composer install") == "ask")
    assert_true(actions.classify("composer require evil/pkg") == "blocked")
    assert_true(actions.classify("composer remove x") == "blocked")


# ------------------------------------------------------------------ read-only DB guard (safety)
@test("db.is_read_only allows reads, refuses writes / DDL / stacking / file I/O")
def _():
    for ok in ["SELECT * FROM catalog_product_entity WHERE sku='x'", "select 1",
               "SHOW TABLES", "DESCRIBE sales_order", "EXPLAIN SELECT 1"]:
        assert_true(db.is_read_only(ok), f"should allow: {ok}")
    for bad in ["INSERT INTO x VALUES(1)", "UPDATE x SET a=1", "DELETE FROM admin_user",
                "DROP TABLE x", "ALTER TABLE x ADD c INT", "TRUNCATE x",
                "SELECT 1; DROP TABLE x", "SELECT a FROM x INTO OUTFILE '/tmp/p'",
                "CREATE TABLE x(a int)", "REPLACE INTO x VALUES(1)", "", "   "]:
        assert_true(not db.is_read_only(bad), f"should refuse: {bad}")


@test("db.run_query refuses a write without touching the DB")
def _():
    r = db.run_query(ROOT, "DELETE FROM admin_user")
    assert_true(r["ok"] is False and "read-only" in r["error"])


@test("db_credentials parses env.php (skipped if php is absent)")
def _():
    if not shutil.which("php"):
        return  # environment has no php — skip; the read-only guard above is the safety-critical part
    d = tempfile.mkdtemp(prefix="magepilot-env-")
    os.makedirs(os.path.join(d, "app", "etc"))
    with open(os.path.join(d, "app", "etc", "env.php"), "w") as f:
        f.write("<?php return ['db'=>['connection'=>['default'=>["
                "'host'=>'db','dbname'=>'magento','username'=>'magento','password'=>'magento']]]];")
    cred = db.db_credentials(d)
    shutil.rmtree(d, ignore_errors=True)
    assert_true(cred and cred["db"] == "magento" and cred["user"] == "magento", f"got {cred}")


# ------------------------------------------------------------------ write engine (edits)
@test("edits.parse_plan parses create / mkdir / edit / delete blocks")
def _():
    text = ("@@MKDIR app/code/Vendor/Mod\n@@END\n"
            "@@CREATE app/code/Vendor/Mod/registration.php\n<?php\n// reg\n@@END\n"
            "@@EDIT app/code/Vendor/Mod/etc/module.xml\n@@FIND\nold\n@@REPLACE\nnew\n@@END\n"
            "@@DELETE app/code/Vendor/Mod/junk.txt\n@@END\n")
    ops = edits.parse_plan(text)
    assert_true([o["op"] for o in ops] == ["mkdir", "create", "edit", "delete"], str(ops))
    assert_in("// reg", ops[1]["content"])
    assert_true(ops[2]["find"] == "old" and ops[2]["replace"] == "new")


@test("edits.apply creates / edits / deletes files")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-edit-")
    edits.apply(d, {"op": "mkdir", "path": "a/b"})
    assert_true(os.path.isdir(os.path.join(d, "a/b")))
    edits.apply(d, {"op": "create", "path": "a/b/x.php", "content": "<?php echo 1;"})
    assert_true(os.path.isfile(os.path.join(d, "a/b/x.php")))
    edits.apply(d, {"op": "edit", "path": "a/b/x.php", "find": "echo 1", "replace": "echo 2"})
    assert_in("echo 2", open(os.path.join(d, "a/b/x.php")).read())
    edits.apply(d, {"op": "delete", "path": "a/b/x.php"})
    assert_true(not os.path.isfile(os.path.join(d, "a/b/x.php")))
    shutil.rmtree(d, ignore_errors=True)


@test("edits.apply refuses paths outside the project (sandbox)")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-edit-")
    assert_raises(tools.ToolError, edits.apply, d, {"op": "create", "path": "../escape.txt", "content": "x"})
    assert_raises(tools.ToolError, edits.apply, d, {"op": "delete", "path": "/etc/passwd"})
    shutil.rmtree(d, ignore_errors=True)


@test("run_make applies approved changes and skips denied ones")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-make-")
    plan = [{"op": "create", "path": "keep.php", "content": "<?php // keep"},
            {"op": "create", "path": "skip.php", "content": "<?php // skip"}]
    orig = scaffold.generate_plan
    scaffold.generate_plan = lambda task, root: plan          # no model in tests
    try:
        decisions = iter(["yes", "no"])
        res = edits.run_make("x", d, approver=lambda op: next(decisions))
    finally:
        scaffold.generate_plan = orig
    assert_true(os.path.isfile(os.path.join(d, "keep.php")), "approved file should exist")
    assert_true(not os.path.isfile(os.path.join(d, "skip.php")), "denied file must NOT exist")
    assert_true(len(res["applied"]) == 1 and len(res["skipped"]) == 1)
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ smart clarify + edit context
@test("edits._extract pulls vendor/name from common phrasings")
def _():
    assert_true(edits._extract("create Vendor_Blog module") == ("Vendor", "Blog"))
    v, n = edits._extract("create a module for vendor Vendor named Blog")
    assert_true(v == "Vendor" and n == "Blog", f"got {(v, n)}")
    assert_true(edits._extract("create a module")[0] is None)


@test("edits.clarify asks for vendor/name only when missing")
def _():
    asked = []
    def asker(q):
        asked.append(q)
        return "Vendor" if "Vendor" in q else "Blog"
    out = edits.clarify("create a module", asker)
    assert_true(len(asked) == 2, "should ask vendor + name")
    assert_in("Vendor: Vendor", out)
    assert_in("name: Blog", out)
    asked.clear()
    edits.clarify("create Vendor_Blog module", asker)
    assert_true(asked == [], "should not ask when vendor+name already given")
    asked.clear()
    assert_true(edits.clarify("what is a module?", asker) == "what is a module?")
    assert_true(asked == [], "questions must not trigger clarify")


@test("edits._context includes the content of a referenced existing file")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-ctx-")
    os.makedirs(os.path.join(d, "app/code/Vendor/Mod"))
    with open(os.path.join(d, "app/code/Vendor/Mod/Foo.php"), "w") as f:
        f.write("<?php\nclass Foo { public function bar() {} }\n")
    assert_in("class Foo", edits._context("add a method to app/code/Vendor/Mod/Foo.php", d))
    assert_in("class Foo", edits._context("edit Foo.php", d))   # basename resolves too
    assert_true(edits._context("create a brand new module", d) == "")
    shutil.rmtree(d, ignore_errors=True)


@test("edits._apply_edit tolerates indentation and re-indents the replacement")
def _():
    cur = "class Foo\n{\n    public function a(): void\n    {\n    }\n}\n"
    find = "public function a(): void\n{\n}"               # model dropped the indentation
    repl = "public function a(): void\n{\n}\n\npublic function b(): void\n{\n}"
    new = edits._apply_edit(cur, find, repl)
    assert_true(new is not None, "fuzzy match should locate the method despite indentation")
    assert_in("function b()", new)
    assert_in("    public function b(): void", new)        # re-indented to the class body (4 spaces)
    assert_true(edits._apply_edit("a=1", "a=1", "a=2") == "a=2")   # exact still works
    assert_true(edits._apply_edit("xyz", "nope", "x") is None)     # unmatchable → None


@test("undo reverts the last make (removes created, restores overwritten/deleted)")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-undo-")
    os.makedirs(os.path.join(d, "app"))
    open(os.path.join(d, "app/keep.php"), "w").write("ORIGINAL")
    open(os.path.join(d, "app/gone.php"), "w").write("DELETE ME")
    plan = [{"op": "create", "path": "app/new.php", "content": "NEW"},
            {"op": "create", "path": "app/keep.php", "content": "OVERWRITTEN"},
            {"op": "delete", "path": "app/gone.php"}]
    orig = scaffold.generate_plan
    scaffold.generate_plan = lambda task, root: plan
    try:
        edits.run_make("x", d, auto=True)
    finally:
        scaffold.generate_plan = orig
    assert_true(os.path.isfile(os.path.join(d, "app/new.php")))
    assert_true(open(os.path.join(d, "app/keep.php")).read().rstrip() == "OVERWRITTEN")  # apply adds \n
    assert_true(not os.path.isfile(os.path.join(d, "app/gone.php")))

    edits.undo(d)
    assert_true(not os.path.isfile(os.path.join(d, "app/new.php")), "created file should be removed")
    assert_true(open(os.path.join(d, "app/keep.php")).read() == "ORIGINAL", "overwrite must be restored")
    assert_true(open(os.path.join(d, "app/gone.php")).read() == "DELETE ME", "deleted file must return")
    assert_true(edits.undo(d) == 0, "second undo should find nothing")   # one level only
    shutil.rmtree(d, ignore_errors=True)


@test("each project directory gets its own isolated index")
def _():
    a = tempfile.mkdtemp(prefix="magepilot-projA-")
    b = tempfile.mkdtemp(prefix="magepilot-projB-")
    open(os.path.join(a, "Alpha.php"), "w").write("<?php\nclass AlphaWidget { public function alpha() {} }\n")
    open(os.path.join(b, "Beta.php"), "w").write("<?php\nclass BetaWidget { public function beta() {} }\n")
    codebase_index.build_index(a, verbose=False)
    codebase_index.build_index(b, verbose=False)
    assert_in("Alpha.php", tools.search_code(a, "alpha widget", k=3))
    assert_in("Beta.php", tools.search_code(b, "beta widget", k=3))
    assert_true("Beta.php" not in tools.search_code(a, "beta widget", k=3), "A's index must not contain B's files")
    assert_true(codebase_index.is_indexed(a) and codebase_index.is_indexed(b))
    shutil.rmtree(a, ignore_errors=True)
    shutil.rmtree(b, ignore_errors=True)


@test("undo removes empty dirs it created but never structural/system dirs")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-undodir-")
    os.makedirs(os.path.join(d, "generated/code/Vendor"))          # system dir — must stay
    open(os.path.join(d, "generated/code/Vendor/x.php"), "w").write("compiled")
    os.makedirs(os.path.join(d, "app/code"))                     # structural — pre-exists
    plan = [{"op": "create", "path": "app/code/Vendor/Mod/registration.php", "content": "<?php"}]
    orig = scaffold.generate_plan
    scaffold.generate_plan = lambda t, r: plan
    try:
        edits.run_make("x", d, auto=True)
    finally:
        scaffold.generate_plan = orig
    assert_true(os.path.isdir(os.path.join(d, "app/code/Vendor/Mod")))
    edits.undo(d)
    assert_true(not os.path.exists(os.path.join(d, "app/code/Vendor/Mod")), "empty created dir removed")
    assert_true(not os.path.exists(os.path.join(d, "app/code/Vendor")), "empty created dir removed")
    assert_true(os.path.isdir(os.path.join(d, "app/code")), "structural app/code must be KEPT")
    assert_true(os.path.isfile(os.path.join(d, "generated/code/Vendor/x.php")), "generated/ must be untouched")
    shutil.rmtree(d, ignore_errors=True)


# ================================================================== v2 framework tests
# ------------------------------------------------------------------ tool registry
@test("registry holds all 8 built-in tools and renders the v1 catalog format")
def _():
    names = tools.REGISTRY.names()
    for n in ("search_code", "grep", "read_file", "find_files", "list_dir",
              "kb_search", "magento_cli", "sql_query"):
        assert_in(n, names)
    cat = tools.tool_catalog()
    assert_in("- search_code: Semantic search over THIS project's indexed code", cat)
    assert_in("- sql_query: Run a READ-ONLY SQL query", cat)


@test("registry refuses duplicate tool names at registration")
def _():
    r = ToolRegistry()
    t = Tool(name="x", description="d", fn=lambda ctx: "ok")
    r.register(t)
    assert_raises(ValueError, r.register, t)


@test("permission gate: MUTATE tools are denied without an approver, run with one")
def _():
    r = ToolRegistry()
    ran = []
    r.register(Tool(name="writeish", description="d", risk=RiskLevel.MUTATE,
                    primary="arg", fn=lambda ctx, arg=None: ran.append(arg) or "did it"))
    out = r.dispatch(ToolContext(root="."), "writeish", "x")          # no approver → deny
    assert_in("requires approval", out)
    assert_true(ran == [], "denied tool must not run")
    out = r.dispatch(ToolContext(root=".", approver=lambda t, a: "yes"), "writeish", "x")
    assert_true(out == "did it" and ran == ["x"])
    out = r.dispatch(ToolContext(root=".", approver=lambda t, a: "no"), "writeish", "y")
    assert_in("requires approval", out)
    assert_true(ran == ["x"], "declined tool must not run")


@test("permission gate: READ tools never consult the approver")
def _():
    consulted = []
    ctx = ToolContext(root=ROOT, approver=lambda t, a: consulted.append(t.name) or "no")
    out = tools.REGISTRY.dispatch(ctx, "grep", "NoSuchEntityException")
    assert_in("FaqRepository.php", out)
    assert_true(consulted == [], "READ tools must not prompt")


@test("Tool.json_schema converts params to a JSON Schema (the MCP path)")
def _():
    t = tools.REGISTRY.get("read_file")
    schema = t.json_schema()
    assert_true(schema["type"] == "object")
    assert_true(schema["properties"]["path"]["type"] == "string")
    assert_true(schema["properties"]["start"]["type"] == "integer")
    assert_true(schema["required"] == ["path"], f"got {schema['required']}")


# ------------------------------------------------------------------ config loader
@test("config loader: defaults give a local provider and v1 role mapping, no cloud")
def _():
    orig = config_loader.USER_CONFIG
    config_loader.USER_CONFIG = os.path.join(_TMP, "nonexistent.toml")
    try:
        cfg = config_loader.load()
    finally:
        config_loader.USER_CONFIG = orig
    assert_true(set(cfg.providers) == {"local"}, "default config must contain NO cloud providers")
    assert_true(cfg.providers["local"].is_remote is False)
    assert_true(cfg.roles["executor"] == "local:auto")
    assert_true(cfg.roles["coder"] == "local:magento")
    assert_true(cfg.roles["planner"] == "@executor")


@test("config loader: project file overrides user file, env overrides both")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-cfg-")
    user_toml = os.path.join(d, "user.toml")
    open(user_toml, "w").write('[roles]\nplanner = "local:user-model"\nreviewer = "local:rev"\n'
                               '[limits]\nmax_task_steps = 5\n')
    proj = os.path.join(d, "proj")
    os.makedirs(proj)
    open(os.path.join(proj, ".magepilot.toml"), "w").write('[roles]\nplanner = "local:proj-model"\n')
    orig = config_loader.USER_CONFIG
    config_loader.USER_CONFIG = user_toml
    env_orig = os.environ.pop("MODEL_SERVER", None)
    try:
        cfg = config_loader.load(proj)
        assert_true(cfg.roles["planner"] == "local:proj-model", "project file must beat user file")
        assert_true(cfg.roles["reviewer"] == "local:rev", "user file keys survive when not overridden")
        assert_true(cfg.limits.max_task_steps == 5)
        os.environ["MODEL_SERVER"] = "http://127.0.0.1:9999/v1"
        cfg2 = config_loader.load(proj)
        assert_true(cfg2.providers["local"].base_url == "http://127.0.0.1:9999/v1", "env must beat files")
    finally:
        config_loader.USER_CONFIG = orig
        os.environ.pop("MODEL_SERVER", None)
        if env_orig is not None:
            os.environ["MODEL_SERVER"] = env_orig
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ model router
def _cfg(**roles) -> Config:
    return Config(
        providers={"local": ProviderCfg(name="local", base_url="http://127.0.0.1:8080/v1"),
                   "anthropic": ProviderCfg(name="anthropic", type="anthropic")},
        roles={"executor": "local:auto", **roles},
    )


@test("router resolves explicit mappings, @aliases, and falls back to executor")
def _():
    r = Router(_cfg(planner="@executor", coder="local:magento"))
    p, m = r.resolve("coder")
    assert_true(p.name == "local" and m == "magento")
    p, m = r.resolve("planner")                       # alias → executor → local:auto
    assert_true(p.name == "local" and m == "auto")
    p, m = r.resolve("summarizer")                    # unmapped role → executor
    assert_true(p.name == "local" and m == "auto")


@test("router refuses alias cycles and unknown providers")
def _():
    r = Router(_cfg(planner="@reviewer", reviewer="@planner"))
    assert_raises(RouterError, r.resolve, "planner")
    r2 = Router(_cfg(planner="nope:model"))
    assert_raises(RouterError, r2.resolve, "planner")


@test("ProviderCfg.is_remote: localhost is local; cloud types and external hosts are remote")
def _():
    assert_true(ProviderCfg(name="a", base_url="http://127.0.0.1:8080/v1").is_remote is False)
    assert_true(ProviderCfg(name="b", base_url="http://localhost:11434/v1").is_remote is False)
    assert_true(ProviderCfg(name="c", base_url="https://api.example.com/v1").is_remote is True)
    assert_true(ProviderCfg(name="d", type="anthropic").is_remote is True)


@test("router model pinning: substring match, auto-pick avoids the fine-tune, strict errors")
def _():
    r = Router(_cfg())
    ids = ["models/qwen2.5-coder-7b-magento-v4", "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"]
    orig = llm_providers.loaded_models
    llm_providers.loaded_models = lambda p: ids
    try:
        local = r.cfg.providers["local"]
        assert_true(r._model_id(local, "magento") == ids[0], "substring match must hit the fine-tune")
        assert_true(r._model_id(local, "auto") == ids[1], "auto must prefer base Instruct, not the fine-tune")
        assert_true(r._model_id(local, "missing-model") == ids[1], "missing model substitutes the auto pick")
        r.cfg.strict_models = True
        assert_raises(RouterError, r._model_id, local, "missing-model")
    finally:
        llm_providers.loaded_models = orig
        r.cfg.strict_models = False


@test("cloud provider types raise a clear not-implemented error (Phase 6)")
def _():
    assert_raises(llm_providers.ProviderError, llm_providers.chat,
                  ProviderCfg(name="anthropic", type="anthropic"), "claude-x",
                  [{"role": "user", "content": "hi"}])


# ================================================================== Phase 2: orchestrator
class _NoModel:
    """Stub router whose complete() always fails — forces deterministic fallbacks."""
    def complete(self, *a, **k):
        raise OSError("no model in tests")


def _no_model():
    return _NoModel()


# ------------------------------------------------------------------ run state
@test("run state: checkpoint round-trips atomically and lists newest first")
def _():
    t1 = run_state.Task(id=1, kind="edit", goal="g1", done_when="d1", note="n1", status="done")
    t2 = run_state.Task(id=2, kind="verify", goal="g2", check={"files_exist": ["a.php"]})
    st = run_state.RunState(run_id=run_state.new_run_id("Test Objective!"),
                            objective="Test Objective!", root="/tmp/x", plan=[t1, t2])
    run_state.save(st)
    loaded = run_state.load(st.run_id)
    assert_true(loaded.objective == st.objective and len(loaded.plan) == 2)
    assert_true(loaded.plan[1].check == {"files_exist": ["a.php"]})
    assert_true(loaded.next_pending().id == 2, "task 1 is done; next pending must be 2")
    assert_in("[edit] g1: n1", loaded.notes_block())
    d = run_state.run_dir(st.run_id)
    assert_true(not [f for f in os.listdir(d) if f.endswith(".tmp")], "atomic write leaves no tmp")
    rows = run_state.list_runs()
    assert_true(rows and rows[0]["run_id"] >= rows[-1]["run_id"], "newest first")


# ------------------------------------------------------------------ planner
@test("planner template: module objective yields edit → command → files-exist verify")
def _():
    name, tasks = planner.plan("Create a Vendor_Faq module with an admin grid")
    assert_true(name == "create_module", f"got template {name!r}")
    kinds = [t.kind for t in tasks]
    assert_true(kinds == ["edit", "command", "verify"], f"got {kinds}")
    assert_true(tasks[1].command == "setup:upgrade")
    assert_in("app/code/Vendor/Faq/registration.php", tasks[2].check["files_exist"])
    assert_in("admin grid", tasks[0].goal)


@test("planner templates match plugin / observer / theme / debug shapes")
def _():
    assert_true(planner.match_template("add a plugin to change the product price")[0] == "add_plugin")
    assert_true(planner.match_template("create an observer for order placement")[0] == "add_observer")
    assert_true(planner.match_template("create a Hyva theme Vendor_Demo")[0] == "create_theme")
    assert_true(planner.match_template("fix this exception in checkout")[0] == "debug")
    assert_true(planner.match_template("how does the cart total work?") is None,
                "questions must fall through to the LLM/single-task path")


@test("planner parses the numbered-line LLM format and extracts commands")
def _():
    text = ("1. [investigate] Find the order placement event | done: event identified\n"
            "garbage line that should be ignored\n"
            "2. [edit] Create the observer and events.xml | done: files created\n"
            "3. [command] Run cache:clean config | done: exit 0\n")
    tasks = planner.parse_llm_plan(text)
    assert_true(len(tasks) == 3 and [t.kind for t in tasks] == ["investigate", "edit", "command"])
    assert_true(tasks[0].done_when == "event identified")
    assert_true(tasks[2].command == "cache:clean config", f"got {tasks[2].command!r}")
    assert_true(planner.parse_llm_plan("no plan here at all") is None)
    many = "\n".join(f"{i}. [verify] t{i} | done: d" for i in range(1, 10))
    assert_true(len(planner.parse_llm_plan(many)) == planner.MAX_TASKS, "plans cap at MAX_TASKS")


@test("planner degrades: LLM failure → single task; kind picked by heuristic")
def _():
    def boom(messages):
        raise OSError("server down")
    name, tasks = planner.plan("which class loads the wishlist sidebar?", complete=boom)
    assert_true(name == "" and len(tasks) == 1 and tasks[0].kind == "investigate")
    name, tasks = planner.plan("implement a wishlist sharing endpoint", complete=boom)
    assert_true(len(tasks) == 1 and tasks[0].kind == "edit", "buildish objective → edit task")
    parseable = "1. [investigate] look | done: found\n2. [edit] change | done: changed"
    name, tasks = planner.plan("implement a wishlist sharing endpoint",
                               complete=lambda m: parseable)
    assert_true(len(tasks) == 2 and tasks[1].kind == "edit")


# ------------------------------------------------------------------ compression
@test("compress.extract_paths pulls file:line refs verbatim, deduped, in order")
def _():
    text = ("Found it in app/code/Vendor/Faq/Model/FaqRepository.php:42 and the wiring in "
            "etc/di.xml. Also app/code/Vendor/Faq/Model/FaqRepository.php:42 again, plus "
            "view/frontend/templates/list.phtml:7-19.")
    paths = compress.extract_paths(text)
    assert_true(paths[0] == "app/code/Vendor/Faq/Model/FaqRepository.php:42")
    assert_true("etc/di.xml" in paths and "view/frontend/templates/list.phtml:7-19" in paths)
    assert_true(len(paths) == 3, f"deduped — got {paths}")


@test("compress.task_note: short text passes through; long text falls back with refs intact")
def _():
    short = "The plugin is wired in etc/di.xml:9."
    assert_true(compress.task_note("g", short) == short)
    orig = compress.get_router
    compress.get_router = _no_model
    try:
        long = ("blah " * 200) + " the key file is app/code/V/M/Plugin/X.php:88 " + ("blah " * 60)
        note = compress.task_note("g", long)
        assert_true(len(note) <= compress.NOTE_MAX_CHARS + 200)
        assert_in("app/code/V/M/Plugin/X.php:88", note, "verbatim path must survive compression")
    finally:
        compress.get_router = orig


# ------------------------------------------------------------------ write tools
@test("write tools are MUTATE/DANGEROUS and hidden from the default ReAct catalog")
def _():
    assert_true(tools.REGISTRY.get("write_file").risk is RiskLevel.MUTATE)
    assert_true(tools.REGISTRY.get("delete_file").risk is RiskLevel.DANGEROUS)
    cat = tools.tool_catalog()
    assert_true("write_file" not in cat and "delete_file" not in cat,
                "default catalog must stay READ-only (v1 prompt unchanged)")
    assert_in("write_file", tools.REGISTRY.catalog(include_mutating=True))


@test("write tools apply with approval, accumulate one undo journal, and undo reverts")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-wt-")
    ctx = ToolContext(root=d, approver=lambda t, a: "yes")
    out = tools.REGISTRY.dispatch(ctx, "write_file",
                                  '{"path": "app/A.php", "content": "<?php // a"}')
    assert_in("wrote", out)
    out = tools.REGISTRY.dispatch(ctx, "write_file",
                                  '{"path": "app/B.php", "content": "<?php // b"}')
    assert_in("wrote", out)
    out = tools.REGISTRY.dispatch(ctx, "edit_file",
                                  '{"path": "app/A.php", "find": "// a", "replace": "// A!"}')
    assert_in("edited", out)
    import json as _json
    journal = _json.load(open(config.UNDO_FILE))
    assert_true(len(journal["ops"]) == 3, f"3 reverses accumulated, got {len(journal['ops'])}")
    edits.undo(d)
    assert_true(not os.path.exists(os.path.join(d, "app/A.php")), "undo removes created files")
    assert_true(not os.path.exists(os.path.join(d, "app/B.php")))
    shutil.rmtree(d, ignore_errors=True)


@test("delete_file is DANGEROUS: denied without an approver, runs with explicit yes")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-del-")
    open(os.path.join(d, "x.php"), "w").write("x")
    out = tools.REGISTRY.dispatch(ToolContext(root=d), "delete_file", "x.php")
    assert_in("requires approval", out)
    assert_true(os.path.isfile(os.path.join(d, "x.php")))
    out = tools.REGISTRY.dispatch(ToolContext(root=d, approver=lambda t, a: "yes"),
                                  "delete_file", "x.php")
    assert_in("deleted", out)
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ orchestrator loop
def _stub_module_plan(task, root):
    return [
        {"op": "create", "path": "app/code/Vendor/Faq/registration.php",
         "content": "<?php // registration"},
        {"op": "create", "path": "app/code/Vendor/Faq/etc/module.xml",
         "content": "<config/>"},
    ]


@test("loop e2e (no model): template plan runs — edit applied, command skipped, verify passes")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-loop-")
    orig_gen, orig_router = scaffold.generate_plan, compress.get_router
    scaffold.generate_plan = _stub_module_plan
    compress.get_router = _no_model
    try:
        run = loop.start("Create a Vendor_Faq module with an admin grid", d)
        assert_true(run.template == "create_module")
        run = loop.run_loop(run, auto=True, verbose=False, limits=LimitsCfg())
    finally:
        scaffold.generate_plan, compress.get_router = orig_gen, orig_router
    assert_true(run.status == "done", f"got {run.status}: " +
                "; ".join(f"{t.kind}={t.status}" for t in run.plan))
    statuses = {t.kind: t.status for t in run.plan}
    assert_true(statuses["edit"] == "done")
    assert_true(statuses["command"] == "skipped", "no bin/magento → graceful skip, not failure")
    assert_true(statuses["verify"] == "done")
    assert_true(os.path.isfile(os.path.join(d, "app/code/Vendor/Faq/registration.php")))
    assert_true(run.answer, "FINISH must produce a summary even without a model")
    events = open(os.path.join(run_state.run_dir(run.run_id), "events.jsonl")).read()
    assert_in('"plan"', events)
    assert_in('"finish"', events)
    shutil.rmtree(d, ignore_errors=True)


@test("loop resume: a paused run continues from its pending task to done")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-resume-")
    open(os.path.join(d, "done.php"), "w").write("x")
    plan = [run_state.Task(id=1, kind="edit", goal="already finished", status="done",
                           note="created done.php"),
            run_state.Task(id=2, kind="verify", goal="confirm the file exists",
                           check={"files_exist": ["done.php"]})]
    paused = run_state.RunState(run_id=run_state.new_run_id("resume test"),
                                objective="resume test", root=d, plan=plan, status="paused")
    run_state.save(paused)
    orig_router = compress.get_router
    compress.get_router = _no_model
    try:
        run = loop.resume(paused.run_id)
        assert_true(run.status == "running")
        run = loop.run_loop(run, auto=True, verbose=False, limits=LimitsCfg())
    finally:
        compress.get_router = orig_router
    assert_true(run.status == "done" and run.plan[1].status == "done",
                f"got {run.status} / {run.plan[1].status}")
    shutil.rmtree(d, ignore_errors=True)


@test("loop budgets and cancellation: zero step budget → budget; pre-set cancel → paused")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-budget-")
    orig_router = compress.get_router
    compress.get_router = _no_model
    try:
        run = loop.start("Create a Vendor_Faq module please", d)
        run = loop.run_loop(run, auto=True, verbose=False,
                            limits=LimitsCfg(max_total_steps=0))
        assert_true(run.status == "budget", f"got {run.status}")

        import threading
        ev = threading.Event()
        ev.set()
        run2 = loop.start("Create a Vendor_Faq module please", d)
        run2 = loop.run_loop(run2, auto=True, verbose=False, cancel=ev, limits=LimitsCfg())
        assert_true(run2.status == "paused", f"got {run2.status}")
        assert_true(run2.next_pending() is not None, "paused run keeps its pending tasks")
        reloaded = run_state.load(run2.run_id)
        assert_true(reloaded.status == "paused", "pause must be checkpointed to disk")
    finally:
        compress.get_router = orig_router
    shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    passed = failed = 0
    print(f"running {len(_results)} deterministic tests (no model)\n")
    for name, fn in _results:
        try:
            fn()
            print(f"  \033[92mPASS\033[0m  {name}")
            passed += 1
        except Exception as e:
            print(f"  \033[91mFAIL\033[0m  {name}\n        {e}")
            if not isinstance(e, AssertionError):
                traceback.print_exc()
            failed += 1
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
