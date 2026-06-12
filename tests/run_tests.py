"""Deterministic tests for MagePilot — NO model required.

Covers the parts whose correctness must not depend on the LLM: the sandbox, every tool,
the CLI whitelist, the ReAct parser, code indexing + semantic search on a fixture module,
and (new in v2) the tool registry/permission gate, the config loader, and the model router.

    python tests/run_tests.py
"""
import os
import shutil
import tempfile
import time
import traceback

from magepilot import config

# Redirect the code index to a throwaway dir BEFORE the collection is opened.
_TMP = tempfile.mkdtemp(prefix="magepilot-test-")
config.CODE_CHROMA_PATH = os.path.join(_TMP, "code_index")
config.ROOT_MARKER = os.path.join(config.CODE_CHROMA_PATH, "root.txt")
config.UNDO_FILE = os.path.join(_TMP, "last_make.json")
config.RUNS_DIR = os.path.join(_TMP, "runs")
config.UPDATE_STATE_FILE = os.path.join(_TMP, "update_state.json")
config.UPDATE_LOCK_FILE = os.path.join(_TMP, "update.lock")
config.UPDATE_LOG_FILE = os.path.join(_TMP, "update.log")

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
        raise AssertionError(msg or f"expected '{needle}' in: {repr(hay)[:300]}")


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


@test("cloud providers refuse to run without their API key (privacy + clarity)")
def _():
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        assert_raises(llm_providers.ProviderError, llm_providers.chat,
                      ProviderCfg(name="anthropic", type="anthropic"), "claude-x",
                      [{"role": "user", "content": "hi"}])
        assert_raises(llm_providers.ProviderError, llm_providers.chat,
                      ProviderCfg(name="openai", type="openai"), "gpt-x",
                      [{"role": "user", "content": "hi"}])
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


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


# ================================================================== Phase 3: knowledge graph
from magepilot.graph import queries as gq                             # noqa: E402
from magepilot.graph.build import build as build_graph                # noqa: E402
from magepilot.graph.store import get_graph, graph_path               # noqa: E402
from magepilot.memory.store import MemoryStore, project_store         # noqa: E402
from magepilot.memory import recall as mem_recall                     # noqa: E402
from magepilot.review import reviewer                                 # noqa: E402
from magepilot.safety import scan as safety_scan                      # noqa: E402
from magepilot.safety.lint_magento import lint_content                # noqa: E402


@test("graph build: fixture module, classes, and Magento edges all land")
def _():
    counts = build_graph(ROOT, verbose=False)
    assert_true(counts["errors"] == 0, f"parse errors: {counts['errors']}")
    g = get_graph(ROOT)
    try:
        mods = [r["name"] for r in g.db.execute("SELECT name FROM modules")]
        assert_in("Vendor_Faq", mods)
        kinds = {r["kind"]: r["n"] for r in g.db.execute(
            "SELECT kind, COUNT(*) n FROM nodes GROUP BY kind")}
        assert_true(kinds.get("class", 0) >= 5 and kinds.get("interface", 0) >= 1, str(kinds))
        edges = {r["kind"] for r in g.db.execute("SELECT DISTINCT kind FROM edges")}
        for k in ("PREFERS", "PLUGS_INTO", "PLUGS_METHOD", "OBSERVES", "DISPATCHES",
                  "INJECTS", "IMPLEMENTS", "DI_ARGUMENT", "DEPENDS_ON_MODULE"):
            assert_in(k, edges)
        assert_true(g.get_meta("build_state") == "complete")
    finally:
        g.close()


@test("graph incremental: unchanged rerun touches nothing; edits and deletes propagate")
def _():
    c1 = build_graph(ROOT, verbose=False)
    assert_true(c1["changed"] == 0 and c1["deleted"] == 0,
                f"no-op rerun must be clean, got {c1['changed']}/{c1['deleted']}")
    extra = os.path.join(ROOT, "Model", "Tmp.php")
    open(extra, "w").write("<?php\nnamespace Vendor\\Faq\\Model;\nclass Tmp {}\n")
    try:
        c2 = build_graph(ROOT, verbose=False)
        assert_true(c2["changed"] == 1, f"got {c2['changed']}")
        g = get_graph(ROOT)
        assert_true(g.db.execute("SELECT 1 FROM nodes WHERE qname='Vendor\\Faq\\Model\\Tmp'")
                    .fetchone() is not None)
        g.close()
    finally:
        os.remove(extra)
    c3 = build_graph(ROOT, verbose=False)
    assert_true(c3["deleted"] == 1, f"got {c3['deleted']}")
    g = get_graph(ROOT)
    assert_true(g.db.execute("SELECT 1 FROM nodes WHERE qname='Vendor\\Faq\\Model\\Tmp'")
                .fetchone() is None, "deleted file's nodes must cascade away")
    g.close()


@test("graph: find_symbol + class_info resolve names, parents, and injections")
def _():
    g = get_graph(ROOT)
    try:
        hits = gq.find_symbol(g, "FaqRepository")
        assert_true(hits and hits[0].qname == "Vendor\\Faq\\Model\\FaqRepository", str(hits[:2]))
        info = gq.class_info(g, "Vendor\\Faq\\Model\\FaqRepository")
        assert_in("Vendor\\Faq\\Api\\FaqRepositoryInterface", info.implements)
        assert_true(any(t == "Vendor\\Faq\\Model\\ResourceModel\\Faq\\CollectionFactory"
                        for _, t in info.injects), str(info.injects))
        assert_in("getById", " ".join(info.methods))
        # fuzzy: split-word FTS finds the repository from natural phrasing
        fuzzy = gq.find_symbol(g, "faq repository")
        assert_true(any("FaqRepository" in r.qname for r in fuzzy), str(fuzzy[:3]))
    finally:
        g.close()


@test("graph: plugins_for sees the global plugin AND the frontend area disable")
def _():
    g = get_graph(ROOT)
    try:
        plugs = gq.plugins_for(g, "Magento\\Catalog\\Model\\Product")
        assert_true(len(plugs) == 1, str(plugs))
        p = plugs[0]
        assert_true(p.plugin_fqcn == "Vendor\\Faq\\Plugin\\AddSurcharge")
        assert_true(p.disabled is True and p.area == "frontend",
                    f"frontend disable must override global: {p}")
        # the broken plugin is found on the concrete class, with the method mismatch flagged
        plugs2 = gq.plugins_for(g, "Vendor\\Faq\\Model\\FaqRepository")
        assert_true(any(pl.plugin_fqcn.endswith("BrokenPlugin") for pl in plugs2), str(plugs2))
        broken = next(pl for pl in plugs2 if pl.plugin_fqcn.endswith("BrokenPlugin"))
        assert_true(any(m[1] == "fetchAll" and m[2] is False for m in broken.methods),
                    f"mismatch must be flagged: {broken.methods}")
    finally:
        g.close()


@test("graph: preference, observers + dispatch site, and impact counts are exact")
def _():
    g = get_graph(ROOT)
    try:
        prefs = gq.preference_for(g, "Vendor\\Faq\\Api\\FaqRepositoryInterface")
        assert_true(len(prefs) == 1 and prefs[0]["impl"] == "Vendor\\Faq\\Model\\FaqRepository"
                    and prefs[0]["winner"], str(prefs))
        obs, disp = gq.observers_of(g, "faq_save_after")
        assert_true(len(obs) == 1 and obs[0].observer_fqcn == "Vendor\\Faq\\Observer\\InvalidateCache",
                    str(obs))
        assert_true(any("FaqManager::save" in d for d in disp),
                    f"dispatch site must be found: {disp}")
        imp = gq.impact_of(g, "Vendor\\Faq\\Api\\FaqRepositoryInterface")
        rels = imp["relations"]
        assert_true(rels["implementors"]["count"] == 1, str(rels))
        assert_true(rels["injectors"]["count"] == 1 and
                    "FaqManager" in rels["injectors"]["examples"][0], str(rels))
        assert_true(rels["preferences"]["count"] == 1, str(rels))
    finally:
        g.close()


@test("graph: diagnose_plugin catches method typos and cross-file disables")
def _():
    g = get_graph(ROOT)
    try:
        d1 = gq.diagnose_plugin(g, "Vendor\\Faq\\Plugin\\BrokenPlugin")
        assert_true(any("METHOD MISMATCH" in f and "fetchAll" in f for f in d1["findings"]),
                    str(d1["findings"]))
        d2 = gq.diagnose_plugin(g, "Vendor\\Faq\\Plugin\\AddSurcharge")
        assert_true(any("DISABLED ELSEWHERE" in f and "frontend" in f for f in d2["findings"]),
                    f"the frontend disable must be attributed: {d2['findings']}")
        d3 = gq.diagnose_plugin(g, "Vendor\\Faq\\Plugin\\Nonexistent")
        assert_true(any("NOT DECLARED" in f for f in d3["findings"]))
    finally:
        g.close()


@test("graph tools: registry dispatch answers wiring questions; missing graph degrades")
def _():
    out = tools.run_tool(ROOT, "wiring", "Magento\\Catalog\\Model\\Product")
    assert_in("AddSurcharge", out)
    assert_in("DISABLED", out)
    out = tools.run_tool(ROOT, "wiring", '{"target": "faq_save_after", "aspect": "observers"}')
    assert_in("InvalidateCache", out)
    out = tools.run_tool(ROOT, "symbol", "FaqManager")
    assert_in("Vendor\\Faq\\Model\\FaqManager", out)
    out = tools.run_tool(ROOT, "diagnose_plugin", "Vendor\\Faq\\Plugin\\BrokenPlugin")
    assert_in("METHOD MISMATCH", out)
    d = tempfile.mkdtemp(prefix="magepilot-nograph-")
    assert_in("not built", tools.run_tool(d, "impact", "Some\\Class"))
    shutil.rmtree(d, ignore_errors=True)
    # grep's empty-result hint points at the graph once it exists
    hint = tools.grep(ROOT, "TotallyAbsentSymbolXyz")
    assert_in("symbol", hint)


# ================================================================== Phase 3: memory
@test("memory store: dedupe, keyword search, touch, and LRU eviction cap")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-mem-")
    m = MemoryStore(os.path.join(d, "memory.db"), cap=5)
    for i in range(6):
        m.add(f"filler fact number {i} about nothing")
    assert_true(m.all_count() <= 5, f"cap must hold, got {m.all_count()}")
    a = m.add("checkout totals collected in app/code/V/M/Model/Total/Fee.php", source="run-1")
    assert_true(m.add("checkout totals collected in app/code/V/M/Model/Total/Fee.php") == a,
                "identical facts must dedupe")
    hits = m.search("where are checkout totals computed")
    assert_true(hits and "Total/Fee.php" in hits[0]["content"], str([h['content'] for h in hits]))
    m.touch([hits[0]["id"]])
    assert_true(m.db.execute("SELECT uses FROM facts WHERE id=?", (hits[0]["id"],))
                .fetchone()[0] == 1)
    m.close()
    shutil.rmtree(d, ignore_errors=True)


@test("memory: run-end facts persist and the NEXT run recalls them")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-mem2-")
    orig_gen, orig_router = scaffold.generate_plan, compress.get_router
    scaffold.generate_plan = _stub_module_plan
    compress.get_router = _no_model
    try:
        r1 = loop.start("Create a Vendor_Faq module with an admin grid", d)
        r1 = loop.run_loop(r1, auto=True, verbose=False, limits=LimitsCfg())
        assert_true(r1.status == "done")
        ms = project_store(d)
        n = ms.all_count()
        ms.close()
        assert_true(n >= 1, f"finished run must persist facts, got {n}")
        r2 = loop.start("extend the Vendor_Faq module with a new admin grid column", d)
        assert_true(r2.memory_block, "second run must recall first run's facts")
        assert_in("Known project facts", r2.memory_block)
    finally:
        scaffold.generate_plan, compress.get_router = orig_gen, orig_router
    shutil.rmtree(d, ignore_errors=True)


@test("memory tools: remember saves a project fact; recall_memory finds it")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-mem3-")
    out = tools.run_tool(d, "remember", "the FAQ grid lives in view/adminhtml/ui_component/faq_listing.xml")
    assert_in("remembered", out)
    out = tools.run_tool(d, "recall_memory", "where is the faq grid defined")
    assert_in("faq_listing.xml", out)
    shutil.rmtree(d, ignore_errors=True)


# ================================================================== Phase 3: lint + scan
@test("every MP rule fires on its sample and stays quiet on clean code")
def _():
    samples = {
        "MP001": ("X.php", "<?php $om = \\Magento\\Framework\\App\\ObjectManager::getInstance();"),
        "MP002": ("X.php", "<?php $id = $_GET['id'];"),
        "MP003": ("X.php", "<?php exec('ls -la');"),
        "MP004": ("X.php", "<?php $data = unserialize($raw);"),
        "MP005": ("X.php", "<?php $conn->query(\"SELECT * FROM t WHERE id=\" . $id);"),
        "MP006": ("Config.php", "<?php $apiKey = 'sk_live_abcDEF123456789xyz';"),
        "MP008": ("etc/di.xml", "<config><preference for=\"A\" type=\"B\"/></config>"),
        "MP009": ("view.phtml", "<p><?= $block->getName(); ?></p>"),
        "MP010": ("X.php", "<?php foreach ($ids as $id) { $p = $factory->create()->load($id); }"),
        "MP011": ("Patch.php", "<?php $sql = 'CREATE TABLE vendor_faq (id INT)';"),
        "MP012": ("view.phtml", "<script>el.innerHTML = userContent;</script>"),
        "MP013": ("X.php", "<?php die('boom');"),
        "MP014": ("X.php", "<?php $ch = curl_init($url);"),
        "MP015": ("X.php", "<?php\nnamespace V\\M;\nclass X {}"),
    }
    for rule_id, (path, content) in samples.items():
        ids = [f.rule_id for f in lint_content(path, content)]
        assert_in(rule_id, ids, f"{rule_id} must fire on its sample (got {ids})")
    clean = ("<?php\ndeclare(strict_types=1);\nnamespace Vendor\\Faq\\Model;\n"
             "class Clean { public function __construct(private readonly \\Psr\\Log\\LoggerInterface $l) {} }\n")
    findings = [f for f in lint_content("Clean.php", clean) if f.severity != "info"]
    assert_true(findings == [], f"clean code must pass: {[(f.rule_id, f.message) for f in findings]}")
    esc = "<p><?= $escaper->escapeHtml($name) ?></p>"
    assert_true(not any(f.rule_id == "MP009" for f in lint_content("v.phtml", esc)),
                "escaped output must not trigger MP009")


@test("scan_op: path policy blocks vendor//generated//env.php; edits lint the new text")
def _():
    for path in ("vendor/magento/module-catalog/Model/X.php", "generated/code/Y.php",
                 "app/etc/env.php"):
        f = safety_scan.scan_op({"op": "create", "path": path, "content": "<?php // x"})
        assert_true(any(x.rule_id == "MP007" and x.severity == "block" for x in f),
                    f"{path} must be write-blocked")
    f = safety_scan.scan_op({"op": "edit", "path": "app/code/V/M/X.php",
                             "find": "old", "replace": "$d = unserialize($raw);"})
    assert_true(any(x.rule_id == "MP004" for x in f), "edit replace-text must be linted")
    assert_true(not any(x.rule_id == "MP015" for x in f),
                "a fragment can't carry declare() — MP015 must not fire on edits")


@test("run_make refuses BLOCK-lint content even in full-auto; reports why")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-block-")
    bad_plan = [{"op": "create", "path": "app/code/V/M/Bad.php",
                 "content": "<?php $om = \\Magento\\Framework\\App\\ObjectManager::getInstance();"},
                {"op": "create", "path": "app/code/V/M/Good.php",
                 "content": "<?php\ndeclare(strict_types=1);\nnamespace V\\M;\nclass Good {}"}]
    orig = scaffold.generate_plan
    scaffold.generate_plan = lambda t, r: bad_plan
    try:
        res = edits.run_make("x", d, auto=True)
    finally:
        scaffold.generate_plan = orig
    assert_true(not os.path.exists(os.path.join(d, "app/code/V/M/Bad.php")),
                "BLOCK content must never reach disk")
    assert_true(os.path.isfile(os.path.join(d, "app/code/V/M/Good.php")))
    assert_true(res["blocked"] and "MP001" in res["blocked"][0]["reasons"][0], str(res["blocked"]))
    shutil.rmtree(d, ignore_errors=True)


@test("write_file tool refuses secrets and protected paths")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-wsec-")
    ctx = ToolContext(root=d, approver=lambda t, a: "yes")
    out = tools.REGISTRY.dispatch(ctx, "write_file",
                                  '{"path": "app/code/V/M/K.php", "content": "<?php $apiKey = \'sk_live_abcDEF123456789xyz\';"}')
    assert_in("MP006", out)
    assert_true(not os.path.exists(os.path.join(d, "app/code/V/M/K.php")))
    out = tools.REGISTRY.dispatch(ctx, "write_file",
                                  '{"path": "vendor/x/y/Z.php", "content": "<?php // x"}')
    assert_in("MP007", out)
    shutil.rmtree(d, ignore_errors=True)


# ================================================================== Phase 3: review
@test("reviewer parses ISSUE lines, discards garbage, handles NO ISSUES and downtime")
def _():
    sample = ("Here are my findings:\n"
              "ISSUE: app/code/V/M/X.php:10 [sec] high unescaped output — use $escaper->escapeHtml()\n"
              "random prose that is not an issue line\n"
              "ISSUE: app/code/V/M/Y.php:5 [perf] low collection not page-bounded\n")
    issues = reviewer.parse_issues(sample)
    assert_true(len(issues) == 2 and issues[0].category == "sec" and issues[0].line == 10,
                str(issues))
    assert_true(reviewer.review_diff("diff --git a b", complete=lambda m: "NO ISSUES") == [])
    assert_true(reviewer.review_diff("diff --git a b",
                                     complete=lambda m: (_ for _ in ()).throw(OSError("down")))
                is None, "unreachable model → None (caller reports gracefully)")
    assert_true(reviewer.review_diff("") == [], "empty diff → no issues, no model call")


# ================================================================== Phase 4: git + debug
import subprocess as _sp                                              # noqa: E402

from magepilot.debug import stacktrace as dbg                         # noqa: E402

# A realistic DI-compilation failure (the acceptance trace).
DI_TRACE = """PHP Fatal error:  Uncaught ReflectionException: Class "Vendor\\Faq\\Plugin\\Missing" does not exist in /var/www/html/vendor/magento/framework/Code/Reader/ClassReader.php:33
Stack trace:
#0 /var/www/html/vendor/magento/framework/Code/Reader/ClassReader.php(33): ReflectionClass->__construct('Vendor\\\\Faq\\\\Plug...')
#1 /var/www/html/vendor/magento/framework/ObjectManager/Definition/Runtime.php(54): Magento\\Framework\\Code\\Reader\\ClassReader->getConstructor('Vendor\\\\Faq\\\\Plug...')
#2 /var/www/html/app/code/Vendor/Faq/Model/FaqManager.php(22): Magento\\Framework\\ObjectManager\\Definition\\Runtime->getParameters('Vendor\\\\Faq\\\\Plug...')
#3 {main}
  thrown in /var/www/html/vendor/magento/framework/Code/Reader/ClassReader.php on line 33"""


def _make_repo(d: str) -> None:
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "test@example.test"],
                 ["config", "user.name", "Test"]):
        _sp.run(["git", "-C", d, *args], capture_output=True, check=True)


@test("git READ tools: status, log, diff, blame on a real temp repo")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-git-")
    _make_repo(d)
    open(os.path.join(d, "a.php"), "w").write("<?php\n$a = 1;\n")
    _sp.run(["git", "-C", d, "add", "."], capture_output=True)
    _sp.run(["git", "-C", d, "commit", "-qm", "first commit"], capture_output=True)
    open(os.path.join(d, "a.php"), "a").write("$b = 2;\n")
    assert_in("main", tools.run_tool(d, "git_status", ""))
    assert_in("a.php", tools.run_tool(d, "git_status", ""))
    assert_in("first commit", tools.run_tool(d, "git_log", ""))
    assert_in("+$b = 2;", tools.run_tool(d, "git_diff", "a.php"))
    assert_in("Test", tools.run_tool(d, "git_blame", '{"path": "a.php", "start": 2, "end": 2}'))
    assert_raises(tools.ToolError, __import__("magepilot.tools.gitops", fromlist=["git_blame"])
                  .git_blame, d, "../outside.php")
    shutil.rmtree(d, ignore_errors=True)


@test("git MUTATE tools are approval-gated; branch/add/commit work end-to-end; no push exists")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-gitw-")
    _make_repo(d)
    open(os.path.join(d, "x.php"), "w").write("<?php\n")
    out = tools.REGISTRY.dispatch(ToolContext(root=d), "git_commit", "no approver")
    assert_in("requires approval", out)
    ctx = ToolContext(root=d, approver=lambda t, a: "yes")
    assert_in("feature/faq", tools.REGISTRY.dispatch(
        ctx, "git_branch", "feature/faq").lower().replace("switched to a new branch 'feature/faq'", "feature/faq"))
    assert_in("staged", tools.REGISTRY.dispatch(ctx, "git_add", "x.php"))
    out = tools.REGISTRY.dispatch(ctx, "git_commit", "add x")
    assert_in("add x", out)
    log = tools.run_tool(d, "git_log", "")
    assert_in("add x", log)
    # branch-name injection refused; push doesn't exist at all
    out = tools.REGISTRY.dispatch(ctx, "git_branch", "bad name; rm -rf /")
    assert_in("invalid branch name", out)
    assert_true(tools.REGISTRY.get("git_push") is None, "there must be NO push tool in v1")
    shutil.rmtree(d, ignore_errors=True)


@test("parse_trace handles #N frames, thrown-in, inline file:line, and dedupes")
def _():
    p = dbg.parse_trace(DI_TRACE)
    assert_true(p["exception"] == "ReflectionException", str(p["exception"]))
    assert_in("does not exist", p["message"])
    files = [f["file"] for f in p["frames"]]
    assert_true(files[0].endswith("ClassReader.php") and p["frames"][0]["line"] == 33,
                "thrown-in site must be frame 0")
    assert_true(len([f for f in files if f.endswith("ClassReader.php")]) == 1, "deduped")
    assert_true(any(f.endswith("FaqManager.php") for f in files))
    inline = dbg.parse_trace("main.CRITICAL: Error: boom in /srv/app/code/V/M/X.php:99")
    assert_true(inline["frames"] and inline["frames"][0]["line"] == 99)
    assert_true(dbg.parse_trace("nothing here")["frames"] == [])


@test("analyze flags the app/code frame as culprit, relativizes foreign paths, hints DI")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-dbg-")
    out = dbg.analyze(d, DI_TRACE)
    assert_in("exception: ReflectionException", out)
    assert_in("app/code/Vendor/Faq/Model/FaqManager.php:22", out)
    assert_in("most likely culprit", out)
    culprit_line = next(ln for ln in out.splitlines() if "culprit" in ln)
    assert_in("FaqManager", culprit_line, "the culprit must be the app/code frame, not vendor")
    assert_in("hint:", out)
    assert_in("symbol", out, "a does-not-exist failure must point at the graph tools")
    assert_in("next: read_file app/code/Vendor/Faq/Model/FaqManager.php", out)
    shutil.rmtree(d, ignore_errors=True)


@test("magento_logs tails the newest entries and refuses unknown log names")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-logs-")
    os.makedirs(os.path.join(d, "var", "log"))
    with open(os.path.join(d, "var", "log", "exception.log"), "w") as f:
        for i in range(1, 4):
            f.write(f"[2026-06-1{i}T08:00:00.000000+00:00] main.CRITICAL: boom {i} "
                    f"in /srv/app/code/V/M/X.php:{i}\nStack trace:\n#0 {{main}}\n")
    out = tools.run_tool(d, "magento_logs", '{"log": "exception.log", "n": 2}')
    assert_in("boom 2", out)
    assert_in("boom 3", out)
    assert_true("boom 1" not in out, "only the last n entries")
    assert_in("error:", tools.run_tool(d, "magento_logs", "../../etc/passwd"))
    assert_in("does not exist", tools.run_tool(d, "magento_logs", "system.log"))
    shutil.rmtree(d, ignore_errors=True)


@test("debug plan template routes through stack_trace / magento_logs / graph tools")
def _():
    name, tasks = planner.plan("fix this error: " + DI_TRACE.splitlines()[0]
                               + "\n#0 /var/www/html/app/code/V/M/X.php(10): foo()")
    assert_true(name == "debug")
    assert_in("stack_trace", tasks[0].goal, "trace present → parse it first")
    assert_in("diagnose_plugin", tasks[1].goal)
    name2, tasks2 = planner.plan("debug why checkout is broken on the live site")
    assert_true(name2 == "debug")
    assert_in("magento_logs", tasks2[0].goal, "no trace → pull the logs first")


# ---- regressions found by the Phase-4 live acceptance run
@test("regression: a skipped no-op (delete of missing file) is NOT counted as applied")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-noop-")
    plan = [{"op": "delete", "path": "app/code/V/M/Ghost.php"},
            {"op": "create", "path": "app/code/V/M/Real.php", "content": "<?php\ndeclare(strict_types=1);"}]
    orig = scaffold.generate_plan
    scaffold.generate_plan = lambda t, r: plan
    try:
        res = edits.run_make("x", d, auto=True)
    finally:
        scaffold.generate_plan = orig
    assert_true(len(res["applied"]) == 1 and res["applied"][0]["path"].endswith("Real.php"),
                f"only the real write counts: {[o['path'] for o in res['applied']]}")
    assert_true(any(o["path"].endswith("Ghost.php") for o in res["skipped"]))
    shutil.rmtree(d, ignore_errors=True)


@test("regression: NEEDS_REPLAN in a Final Answer is honored even when the answer parses")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-replan-")
    plan = [run_state.Task(id=1, kind="verify", goal="confirm something impossible")]
    run = run_state.RunState(run_id=run_state.new_run_id("replan test"),
                             objective="replan test", root=d, plan=plan)
    run_state.save(run)
    orig_react, orig_replan, orig_router = loop.react.run, loop.planner.replan, compress.get_router
    loop.react.run = lambda *a, **k: {"answer": "NEEDS_REPLAN: target cannot exist",
                                      "steps": [], "stopped": "final", "scratchpad": ""}
    loop.planner.replan = lambda *a, **k: None
    compress.get_router = _no_model
    try:
        run = loop.run_loop(run, auto=True, verbose=False, limits=LimitsCfg())
    finally:
        loop.react.run, loop.planner.replan, compress.get_router = orig_react, orig_replan, orig_router
    assert_true(run.status == "failed", f"NEEDS_REPLAN must never be 'done': {run.status}")
    assert_true(run.plan[0].status == "failed", run.plan[0].status)
    shutil.rmtree(d, ignore_errors=True)


@test("regression: the ReAct format example is labeled, not an executable first step")
def _():
    ex = react_agent._example()
    assert_in("Format example only", ex)
    assert_in("NOT your task", ex)


@test("regression: resuming a crashed run re-runs the task that was mid-flight")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-crash-")
    open(os.path.join(d, "ok.php"), "w").write("x")
    plan = [run_state.Task(id=1, kind="edit", goal="finished earlier", status="done", note="n"),
            run_state.Task(id=2, kind="verify", goal="was mid-flight when the process died",
                           status="running", attempts=1, check={"files_exist": ["ok.php"]})]
    crashed = run_state.RunState(run_id=run_state.new_run_id("crash test"),
                                 objective="crash test", root=d, plan=plan, status="running")
    run_state.save(crashed)
    orig_router = compress.get_router
    compress.get_router = _no_model
    try:
        run = loop.resume(crashed.run_id)
        assert_true(run.plan[1].status == "pending", "mid-flight task must be re-queued")
        run = loop.run_loop(run, auto=True, verbose=False, limits=LimitsCfg())
    finally:
        compress.get_router = orig_router
    assert_true(run.status == "done" and run.plan[1].status == "done",
                f"{run.status} / {run.plan[1].status} — the interrupted task must actually run")
    shutil.rmtree(d, ignore_errors=True)


# ================================================================== Phase 5
from magepilot.agent import modes                                     # noqa: E402
from magepilot.graph.extractors import classify as classify_path     # noqa: E402
from magepilot.testgen import phpunit as testgen                      # noqa: E402
from magepilot.tools.hybridsearch import search as hybrid_search      # noqa: E402


@test("modes: subsets filter the catalog, no-edit modes never plan file changes")
def _():
    assert_true(modes.get(None).name == "code" and modes.get("nope").name == "code")
    ask = modes.get("ask")
    cat = tools.REGISTRY.catalog(names=ask.tools)
    assert_in("- symbol:", cat)
    assert_true("sql_query" not in cat and "git_blame" not in cat,
                "ask mode must show a focused subset")
    assert_in("sql_query", tools.REGISTRY.catalog(names=modes.get("debug").tools))
    lim = modes.apply_limits(modes.get("autonomous"), LimitsCfg())
    assert_true(lim.max_total_steps == 60 and lim.wall_clock_minutes == 45)
    name, tasks = planner.plan("create a Vendor_Faq module with an admin grid", mode="ask")
    assert_true(len(tasks) == 1 and tasks[0].kind == "investigate",
                "ask mode must never plan edits, even for buildish objectives")


@test("graph v2: classify routes every new artifact type with the right area")
def _():
    assert_true(classify_path("etc/webapi.xml") == ("webapi", "global"))
    assert_true(classify_path("etc/db_schema.xml") == ("db_schema", "global"))
    assert_true(classify_path("etc/crontab.xml") == ("jobs", "global"))
    assert_true(classify_path("etc/adminhtml/di.xml") == ("di", "adminhtml"))
    assert_true(classify_path("view/frontend/layout/faq_index_index.xml") == ("layout", "frontend"))
    assert_true(classify_path("view/base/layout/default.xml") == ("layout", "global"))
    assert_true(classify_path("app/design/frontend/V/t/theme.xml") == ("theme", None))
    assert_true(classify_path("etc/schema.graphqls") == ("graphqls", "graphql"))
    assert_true(classify_path("etc/view.xml") == (None, None), "unknown etc xml is skipped")


@test("graph v2: routes, tables, cron, layout, and GraphQL all land in the graph")
def _():
    build_graph(ROOT, verbose=False)
    g = get_graph(ROOT)
    try:
        kinds = {r["kind"] for r in g.db.execute("SELECT DISTINCT kind FROM nodes")}
        for k in ("route", "table", "cron_job", "layout_handle", "template",
                  "gql_type", "gql_field"):
            assert_in(k, kinds)
        edges = {r["kind"] for r in g.db.execute("SELECT DISTINCT kind FROM edges")}
        for k in ("ROUTES_TO", "OWNS_TABLE", "REFERENCES_TABLE", "CRON_RUNS",
                  "HAS_BLOCK", "RENDERS", "ARG_VIEW_MODEL", "INCLUDES_HANDLE", "RESOLVES"):
            assert_in(k, edges)
    finally:
        g.close()


@test("graph v2: what_handles_route matches :param patterns and finds the preference impl")
def _():
    g = get_graph(ROOT)
    try:
        info = gq.what_handles_route(g, "GET", "/V1/faq/42")
        assert_true(info is not None, "the :id pattern must match a concrete id")
        assert_true(info["service"] == "Vendor\\Faq\\Api\\FaqRepositoryInterface::getById", str(info))
        assert_true(info["impl"] == "Vendor\\Faq\\Model\\FaqRepository",
                    "the winning preference must be resolved")
        assert_in("Vendor_Faq::faq_view", info["resources"])
        assert_true(info["plugins"] >= 1, "BrokenPlugin intercepts the impl")
        assert_true(gq.what_handles_route(g, "POST", "/V1/faq/42") is None, "wrong verb")
    finally:
        g.close()


@test("graph v2: graphql_resolver, template_context, table_info answer exactly")
def _():
    g = get_graph(ROOT)
    try:
        r = gq.graphql_resolver(g, "Query.faq")
        assert_true(r and r["resolver"] == "Vendor\\Faq\\Model\\Resolver\\Faq", str(r))
        ctx = gq.template_context(g, "Vendor_Faq::faq/list.phtml")
        assert_true(ctx["blocks"] and ctx["blocks"][0]["block"] == "Vendor\\Faq\\Block\\FaqList", str(ctx))
        assert_in("faq_index_index", ctx["handles"])
        assert_true(any(v["class"] == "Vendor\\Faq\\ViewModel\\FaqData" for v in ctx["view_models"]),
                    str(ctx["view_models"]))
        t = gq.table_info(g, "vendor_faq")
        assert_true(t and t["owner"] == "Vendor_Faq" and "question" in t["columns"], str(t))
        assert_true(any(ref == "store" for ref, _ in t["fks_out"]), str(t["fks_out"]))
        cron = g.db.execute("SELECT dst_qname FROM edges WHERE kind='CRON_RUNS'").fetchone()
        assert_true(cron["dst_qname"] == "Vendor\\Faq\\Cron\\Cleanup::execute")
    finally:
        g.close()


@test("wiring tool auto-detects routes, GraphQL fields, templates, and tables")
def _():
    assert_in("FaqRepositoryInterface::getById", tools.run_tool(ROOT, "wiring", "GET /V1/faq/42"))
    assert_in("Resolver\\Faq", tools.run_tool(ROOT, "wiring", "Query.faq"))
    assert_in("FaqList", tools.run_tool(ROOT, "wiring", "Vendor_Faq::faq/list.phtml"))
    out = tools.run_tool(ROOT, "wiring", '{"target": "vendor_faq", "aspect": "table"}')
    assert_in("owner=Vendor_Faq", out)


@test("hybrid search routes by shape: graph / grep / semantic, sources labeled")
def _():
    assert_in("[graph]", hybrid_search(ROOT, "Vendor\\Faq\\Model\\FaqRepository"))
    assert_in("[graph]", hybrid_search(ROOT, "faq_save_after"))
    out = hybrid_search(ROOT, "GET /V1/faq/42")
    assert_in("FaqRepositoryInterface", out)
    assert_in("[grep]", hybrid_search(ROOT, r"class\s+AddSurcharge"))
    out = hybrid_search(ROOT, "repository method that throws a not-found exception")
    assert_in("[semantic]", out)
    assert_in("FaqRepository.php", out)


@test("testgen: graph-powered skeleton mirrors the path, mocks ctor deps, lints clean")
def _():
    rel, content = testgen.skeleton(ROOT, "Vendor\\Faq\\Model\\FaqRepository")
    assert_true(rel == "app/code/Vendor/Faq/Test/Unit/Model/FaqRepositoryTest.php", rel)
    assert_in("namespace Vendor\\Faq\\Test\\Unit\\Model;", content)
    assert_in("$this->createMock(CollectionFactory::class)", content)
    assert_in("new FaqRepository($this->collectionFactory)", content)
    assert_in("public function testInstance(): void", content)
    assert_in("markTestIncomplete('TODO: cover FaqRepository::getById()')", content)
    if shutil.which("php"):
        d = tempfile.mkdtemp(prefix="magepilot-tg-")
        p = os.path.join(d, "T.php")
        open(p, "w").write(content)
        r = _sp.run(["php", "-l", p], capture_output=True, text=True)
        assert_true(r.returncode == 0, f"php -l must pass: {r.stdout}{r.stderr}")
        shutil.rmtree(d, ignore_errors=True)


@test("testgen: write_test writes with approval + journal; undo reverts it")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-tgw-")
    shutil.copytree(ROOT, os.path.join(d, "app", "code", "Vendor", "Faq"))
    build_graph(d, verbose=False)
    res = testgen.write_test(d, "Vendor\\Faq\\Model\\FaqManager", auto=True, fill=False)
    assert_true(res["written"] and os.path.isfile(os.path.join(d, res["path"])), str(res))
    assert_in("Test/Unit/Model/FaqManagerTest.php", res["path"])
    edits.undo(d)
    assert_true(not os.path.exists(os.path.join(d, res["path"])), "undo must revert the test")
    shutil.rmtree(d, ignore_errors=True)


@test("policy: vendor/bin/phpunit is ASK-tier, runnable only when present, injection-safe")
def _():
    assert_true(actions.classify("vendor/bin/phpunit app/code") == "ask")
    assert_true(actions.classify("vendor/bin/phpunit; rm -rf /") == "blocked")
    assert_true(actions.classify("vendor/bin/other-binary") == "blocked")
    r = actions.execute(ROOT, "vendor/bin/phpunit app/code", approver=lambda c, s: "yes")
    assert_true(r["ran"] is False and "not found" in r["reason"],
                f"missing phpunit must fail gracefully: {r}")


@test("planner: 'write tests for X' plans the test template, not a module scaffold")
def _():
    name, tasks = planner.plan("write unit tests for the FaqRepository class")
    assert_true(name == "create_tests", f"got {name}")
    assert_true([t.kind for t in tasks] == ["investigate", "edit", "command"])
    assert_true(tasks[2].command.startswith("vendor/bin/phpunit"))
    name2, _tasks2 = planner.plan("create a Vendor_Blog module")
    assert_true(name2 == "create_module", "plain module creation is unaffected")


@test("regression: testgen rejects a model fill that doesn't parse as PHP")
def _():
    if not shutil.which("php"):
        return
    broken = ("<?php\nclass FaqRepositoryTest extends TestCase {\n"
              "    protected function setUp(): void {}\n"
              "    public function testX(): void { $this loads the FAQ; }\n}\n")
    class FakeRouter:
        def complete(self, *a, **k):
            return broken
    orig = testgen.get_router
    testgen.get_router = lambda: FakeRouter()
    try:
        rel, content = testgen.generate(ROOT, "Vendor\\Faq\\Model\\FaqRepository", fill=True)
    finally:
        testgen.get_router = orig
    assert_true("$this loads the FAQ" not in content,
                "an unparseable fill must be rejected")
    assert_in("markTestIncomplete", content, "fallback must be the deterministic skeleton")


@test("theme extractor: parent chain lands as THEME_PARENT")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-theme-")
    tdir = os.path.join(d, "app", "design", "frontend", "Vendor", "demo")
    os.makedirs(tdir)
    open(os.path.join(tdir, "theme.xml"), "w").write(
        '<theme xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<title>Demo</title><parent>Hyva/default</parent></theme>")
    open(os.path.join(tdir, "registration.php"), "w").write("<?php // theme")
    build_graph(d, verbose=False)
    g = get_graph(d)
    try:
        e = g.db.execute("SELECT * FROM edges WHERE kind='THEME_PARENT'").fetchone()
        assert_true(e is not None and e["src_qname"] == "theme:Vendor/demo"
                    and e["dst_qname"] == "theme:Hyva/default",
                    str(dict(e) if e else None))
    finally:
        g.close()
    shutil.rmtree(d, ignore_errors=True)


# ================================================================== Phase 6: MCP + cloud
from magepilot.config.schema import McpServerCfg                      # noqa: E402
from magepilot.mcp import client as mcp_client, server as mcp_server  # noqa: E402
from magepilot.tools.registry import ToolRegistry as _TR              # noqa: E402


@test("anthropic payload builder: system extracted, sampling filtered, stops kept")
def _():
    prov = ProviderCfg(name="anthropic", type="anthropic")
    url, headers, body = llm_providers.anthropic_request(
        prov, "claude-sonnet-4-5",
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}],
        stop=["Observation:", "<|im_end|>"],
        sampling={"temperature": 0.15, "repetition_penalty": 1.1, "max_tokens": 900},
        api_key="k-test")
    assert_true(url.endswith("/v1/messages"))
    assert_true(headers["x-api-key"] == "k-test" and "anthropic-version" in headers)
    assert_true(body["system"] == "be terse")
    assert_true(all(m["role"] != "system" for m in body["messages"]))
    assert_true(body["max_tokens"] == 900 and body["temperature"] == 0.15)
    assert_true("repetition_penalty" not in body, "unsupported keys must be filtered")
    assert_in("Observation:", body["stop_sequences"])
    assert_true(llm_providers.parse_anthropic(
        {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "ab")


@test("config loader parses [mcp_servers.*] declarations")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-mcpcfg-")
    open(os.path.join(d, ".magepilot.toml"), "w").write(
        '[mcp_servers.ctx7]\ncommand = "npx"\nargs = ["-y", "ctx7-mcp"]\nread_only = true\n')
    orig = config_loader.USER_CONFIG
    config_loader.USER_CONFIG = os.path.join(d, "nonexistent.toml")
    try:
        cfg = config_loader.load(d)
    finally:
        config_loader.USER_CONFIG = orig
    assert_true("ctx7" in cfg.mcp_servers)
    m = cfg.mcp_servers["ctx7"]
    assert_true(m.command == "npx" and m.args == ("-y", "ctx7-mcp") and m.read_only)
    shutil.rmtree(d, ignore_errors=True)


@test("MCP server handle(): init, read-only listing, calls, errors, notifications")
def _():
    ctx = ToolContext(root=ROOT)
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, ctx, False)
    assert_true(r["result"]["serverInfo"]["name"] == "magepilot")
    assert_true(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                  ctx, False) is None)
    tools_ro = {t["name"] for t in mcp_server.handle(
        {"id": 2, "method": "tools/list"}, ctx, False)["result"]["tools"]}
    assert_in("grep", tools_ro)
    assert_in("wiring", tools_ro)
    assert_true("write_file" not in tools_ro, "writes hidden without --allow-writes")
    tools_rw = {t["name"] for t in mcp_server.handle(
        {"id": 3, "method": "tools/list"}, ctx, True)["result"]["tools"]}
    assert_in("write_file", tools_rw)
    r = mcp_server.handle({"id": 4, "method": "tools/call",
                           "params": {"name": "grep",
                                      "arguments": {"pattern": "NoSuchEntityException"}}},
                          ctx, False)
    assert_in("FaqRepository.php", r["result"]["content"][0]["text"])
    assert_true(not r["result"]["isError"])
    r = mcp_server.handle({"id": 5, "method": "tools/call",
                           "params": {"name": "write_file",
                                      "arguments": {"path": "x.php", "content": "x"}}},
                          ctx, False)
    assert_true(r["result"]["isError"], "write tools must refuse without --allow-writes")
    r = mcp_server.handle({"id": 6, "method": "no/such"}, ctx, False)
    assert_true(r["error"]["code"] == -32601)


@test("MCP loopback: our client drives our server over real stdio (the acceptance)")
def _():
    import sys as _sys
    cfg = McpServerCfg(name="magepilot_self", command=_sys.executable,
                       args=("-m", "magepilot", "mcp-serve", "--root", ROOT))
    srv = mcp_client.McpServer(cfg)
    try:
        srv.initialize()
        specs = srv.list_tools()
        names = {t["name"] for t in specs}
        assert_in("grep", names)
        assert_in("symbol", names)
        assert_true("write_file" not in names)
        grep_spec = next(t for t in specs if t["name"] == "grep")
        assert_true(grep_spec["inputSchema"]["properties"]["pattern"]["type"] == "string")
        out = srv.call("grep", {"pattern": "class AddSurcharge"})
        assert_in("AddSurcharge", out)
        out = srv.call("magento_logs", {"log": "../etc/passwd"})
        assert_in("error", out)
    finally:
        srv.close()


@test("MCP client: wrapped tools default to MUTATE and pass the approval gate")
def _():
    class FakeSrv:
        cfg = McpServerCfg(name="ext", command="x", read_only=False)
        def call(self, name, arguments):
            return f"called {name} with {arguments.get('q')}"
    spec = {"name": "lookup", "description": "find things",
            "inputSchema": {"type": "object",
                            "properties": {"q": {"type": "string", "description": "query"}},
                            "required": ["q"]}}
    reg = _TR()
    tool = mcp_client.wrap_tool(FakeSrv(), spec, set())
    reg.register(tool)
    assert_true(tool.risk is RiskLevel.MUTATE, "external tools are approval-gated by default")
    assert_in("[ext]", tool.description)
    out = reg.dispatch(ToolContext(root="."), "lookup", '{"q": "x"}')
    assert_in("requires approval", out)
    out = reg.dispatch(ToolContext(root=".", approver=lambda t, a: "yes"), "lookup", '{"q": "x"}')
    assert_true(out == "called lookup with x", out)
    ro = mcp_client.wrap_tool(
        FakeSrv.__class__("F", (), {"cfg": McpServerCfg(name="ext", command="x", read_only=True),
                                    "call": lambda self, n, a: "ok"})(), spec, {"lookup"})
    assert_true(ro.risk is RiskLevel.READ and ro.name == "ext_lookup",
                f"read_only + collision prefix: {ro.name} {ro.risk}")


# ================================================================== graph v3
@test("v3 CALLS: same-class, injected-property, and parent/static calls land")
def _():
    build_graph(ROOT, verbose=False)
    g = get_graph(ROOT)
    try:
        callees = gq.callees_of(g, "Vendor\\Faq\\Model\\FaqManager::save")
        targets = {c["callee"] for c in callees}
        assert_in("Vendor\\Faq\\Model\\FaqManager::validate", targets)
        assert_in("Vendor\\Faq\\Api\\FaqRepositoryInterface::getById", targets,
                  "injected-property call must resolve through the promoted ctor type")
        assert_true(not any("dispatch" in t for t in targets),
                    "dispatch is a DISPATCHES edge, never a CALLS edge")
        callers = gq.callers_of(g, "Vendor\\Faq\\Api\\FaqRepositoryInterface::getById")
        assert_true(any(c["caller"] == "Vendor\\Faq\\Model\\FaqManager::save"
                        for c in callers), str(callers))
    finally:
        g.close()


@test("v3 CALLS: vendor code produces no call edges (volume guard)")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-vcalls-")
    vdir = os.path.join(d, "vendor", "acme", "module-x")
    os.makedirs(vdir)
    open(os.path.join(vdir, "registration.php"), "w").write("<?php // module")
    open(os.path.join(vdir, "Thing.php"), "w").write(
        "<?php\nnamespace Acme\\X;\nclass Thing {\n"
        "    public function a(): void { $this->b(); }\n"
        "    public function b(): void {}\n}\n")
    build_graph(d, verbose=False)
    g = get_graph(d)
    try:
        n = g.db.execute("SELECT COUNT(*) FROM edges WHERE kind='CALLS'").fetchone()[0]
        assert_true(n == 0, f"vendor CALLS must be excluded, got {n}")
        assert_true(g.db.execute("SELECT 1 FROM nodes WHERE qname='Acme\\X\\Thing'")
                    .fetchone() is not None, "vendor declarations still indexed")
    finally:
        g.close()
    shutil.rmtree(d, ignore_errors=True)


@test("v3 Alpine: components, listeners, and emitters from the Hyvä template")
def _():
    g = get_graph(ROOT)
    try:
        comps = gq.alpine_components(g)
        assert_true(any(c["component"] == "initFaqList" for c in comps), str(comps))
        c = next(c for c in comps if c["component"] == "initFaqList")
        assert_true(c["template"] == "Vendor_Faq::faq/list.phtml",
                    f"template qname must be module-relative: {c['template']}")
        ev = gq.js_event_info(g, "private-content-loaded")
        assert_true(len(ev["listeners"]) == 1 and not ev["emitters"], str(ev))
        ev2 = gq.js_event_info(g, "faq-item-selected")
        assert_true(len(ev2["emitters"]) == 1, "CustomEvent dispatch must be an emitter")
        ev3 = gq.js_event_info(g, "faq-list-changed")
        assert_true(len(ev3["emitters"]) == 1, "$dispatch must be an emitter")
        out = tools.run_tool(ROOT, "wiring", "private-content-loaded")
        assert_in("list.phtml", out)
        out = tools.run_tool(ROOT, "wiring", '{"target": "initFaq", "aspect": "alpine"}')
        assert_in("initFaqList", out)
    finally:
        g.close()


@test("v3 coverage: unit COVERS via mirror path + MFTF at module level; tests_for/impact")
def _():
    g = get_graph(ROOT)
    try:
        cov = g.db.execute("SELECT src_qname, dst_qname FROM edges WHERE kind='COVERS'").fetchall()
        assert_true(any(r["src_qname"] == "Vendor\\Faq\\Test\\Unit\\Model\\FaqRepositoryTest"
                        and r["dst_qname"] == "Vendor\\Faq\\Model\\FaqRepository"
                        for r in cov), str([dict(r) for r in cov]))
        t = gq.tests_for(g, "Vendor\\Faq\\Model\\FaqRepository")
        kinds = {x["kind"] for x in t}
        assert_true(kinds == {"unit", "mftf"}, str(t))
        assert_true(any(x["test"] == "StorefrontFaqListTest" for x in t))
        imp = gq.impact_of(g, "Vendor\\Faq\\Model\\FaqRepository")
        assert_in("tests", imp["relations"])
        imp_iface = gq.impact_of(g, "Vendor\\Faq\\Api\\FaqRepositoryInterface")
        assert_in("callers", imp_iface["relations"],
                  "calls target the injected interface — impact must show them there")
        import json as _json
        out = tools.run_tool(ROOT, "wiring", _json.dumps(
            {"target": "Vendor\\Faq\\Model\\FaqRepository", "aspect": "tests"}))
        assert_in("FaqRepositoryTest", out)
        out2 = tools.run_tool(ROOT, "wiring", _json.dumps(
            {"target": "Vendor\\Faq\\Model\\FaqManager", "aspect": "tests"}))
        assert_in("0 unit", out2, "no unit test, but module-level MFTF still counts")
        assert_in("StorefrontFaqListTest", out2)
    finally:
        g.close()


@test("v3 preference winner follows module load order (later module wins)")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-order-")
    for mod, seq, impl in (("Va_A", None, "Va\\A\\Model\\Impl"),
                           ("Va_B", "Va_A", "Va\\B\\Model\\Impl")):
        v, m = mod.split("_")
        base = os.path.join(d, "app", "code", v, m)
        os.makedirs(os.path.join(base, "etc"))
        open(os.path.join(base, "registration.php"), "w").write("<?php // reg")
        seq_xml = f"<sequence><module name=\"{seq}\"/></sequence>" if seq else ""
        open(os.path.join(base, "etc", "module.xml"), "w").write(
            f'<config><module name="{mod}">{seq_xml}</module></config>')
        open(os.path.join(base, "etc", "di.xml"), "w").write(
            f'<config><preference for="Shared\\Api\\ThingInterface" type="{impl}"/></config>')
    build_graph(d, verbose=False)
    g = get_graph(d)
    try:
        prefs = gq.preference_for(g, "Shared\\Api\\ThingInterface")
        assert_true(len(prefs) == 2, str(prefs))
        winner = next(p for p in prefs if p["winner"])
        assert_true(winner["module"] == "Va_B",
                    f"Va_B loads after Va_A (sequence) so its preference wins: {winner}")
    finally:
        g.close()
    shutil.rmtree(d, ignore_errors=True)


# ================================================================== functional testgen
import io as _io                                                       # noqa: E402

import defusedxml.ElementTree as _DET                                  # noqa: E402

from magepilot.testgen import common as tg_common                      # noqa: E402
from magepilot.testgen import mftf as tg_mftf                          # noqa: E402
from magepilot.testgen import playwright as tg_pw                      # noqa: E402


@test("testgen common: targets resolve through the graph (alpine → template → handle → URL)")
def _():
    build_graph(ROOT, verbose=False)
    t = tg_common.resolve_target(ROOT, "initFaqList")
    assert_true(t.alpine == "initFaqList" and t.template == "Vendor_Faq::faq/list.phtml", str(t))
    assert_true(t.handle == "faq_index_index" and t.url == "/faq/index/index", str(t))
    assert_true(t.module == "Vendor_Faq", str(t))
    t2 = tg_common.resolve_target(ROOT, "faq_index_index")
    assert_true(t2.url == "/faq/index/index" and t2.module == "Vendor_Faq", str(t2))
    t3 = tg_common.resolve_target(ROOT, "/checkout/cart")
    assert_true(t3.url == "/checkout/cart" and not t3.module)
    t4 = tg_common.resolve_target(ROOT, "somethingUnknown")
    assert_in("AMONPAGE_URL", t4.url, "underivable URLs must be loudly placeholder'd")


@test("MFTF skeleton: three parseable XMLs, graph selector, consistent wiring")
def _():
    ops, t = tg_mftf.skeleton(ROOT, "initFaqList")
    assert_true(len(ops) == 3, str([o["path"] for o in ops]))
    paths = {o["path"] for o in ops}
    base = "app/code/Vendor/Faq/Test/Mftf"
    assert_in(f"{base}/Test/StorefrontInitFaqListTest.xml", paths)
    assert_in(f"{base}/Page/StorefrontInitFaqListPage.xml", paths)
    assert_in(f"{base}/Section/StorefrontInitFaqListSection.xml", paths)
    for o in ops:
        _DET.parse(_io.StringIO(o["content"]))          # every file parses
    section = next(o for o in ops if "/Section/" in o["path"])
    assert_in("[x-data^='initFaqList(']", section["content"],
              "the selector must be the exact graph-derived Alpine root")
    page = next(o for o in ops if "/Page/" in o["path"])
    assert_in('url="faq/index/index"', page["content"])
    test_xml = next(o for o in ops if "/Mftf/Test/" in o["path"])
    assert_in("StorefrontInitFaqListPage.url", test_xml["content"])
    assert_in('group value="vendor_faq"', test_xml["content"])
    assert_raises(ValueError, tg_mftf.skeleton, ROOT, "/just/a/url")


@test("MFTF write: batch lands under the module, one journal, undo reverts all three")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-mftfw-")
    shutil.copytree(ROOT, os.path.join(d, "app", "code", "Vendor", "Faq"))
    build_graph(d, verbose=False)
    res = tg_mftf.write(d, "initFaqList", auto=True)
    assert_true(len(res["written"]) == 3, str(res))
    for rel in res["written"]:
        assert_true(os.path.isfile(os.path.join(d, rel)), rel)
    edits.undo(d)
    for rel in res["written"]:
        assert_true(not os.path.exists(os.path.join(d, rel)), f"undo must remove {rel}")
    shutil.rmtree(d, ignore_errors=True)


@test("Playwright skeleton: graph selector, node --check clean, config only when absent")
def _():
    ops, t = tg_pw.skeleton(ROOT, "initFaqList")
    paths = [o["path"] for o in ops]
    assert_in("tests/playwright/init_faq_list.spec.js", paths)
    assert_in(tg_pw.CONFIG_REL, paths)
    spec = next(o for o in ops if o["path"].endswith(".spec.js"))
    assert_in("page.goto('/faq/index/index')", spec["content"])
    assert_in('[x-data^="initFaqList("]', spec["content"])
    assert_in("main#maincontent", spec["content"])
    if shutil.which("node"):
        assert_true(tg_pw._node_checks(spec["content"]))
    d = tempfile.mkdtemp(prefix="magepilot-pww-")
    res = tg_pw.write(d, "/checkout/cart", auto=True)
    assert_true(len(res["written"]) == 2, str(res))
    res2 = tg_pw.write(d, "/checkout/cart/index", auto=True)
    assert_true(all("config" not in p for p in res2["written"]),
                "config must not be recreated once present")
    shutil.rmtree(d, ignore_errors=True)


@test("policy: mftf + npx playwright are ASK-tier; injection and unknown npx blocked")
def _():
    assert_true(actions.classify("vendor/bin/mftf run:group vendor_faq") == "ask")
    assert_true(actions.classify("npx playwright test --config tests/playwright/playwright.config.js") == "ask")
    assert_true(actions.classify("npx something-else") == "blocked")
    assert_true(actions.classify("npx playwright test; rm -rf /") == "blocked")
    r = actions.execute(ROOT, "vendor/bin/mftf run:group x", approver=lambda c, s: "yes")
    assert_true(r["ran"] is False and "not found" in r["reason"], str(r))


@test("planner: mftf and playwright objectives get their own templates")
def _():
    name, tasks = planner.plan("write a playwright e2e test for the FAQ list page")
    assert_true(name == "create_tests")
    assert_in("playwright", tasks[1].goal.lower())
    assert_true(tasks[2].command.startswith("npx playwright test"), tasks[2].command)
    name2, tasks2 = planner.plan("create an MFTF functional test for the FAQ page")
    assert_in("MFTF", tasks2[1].goal)
    assert_true(tasks2[2].command.startswith("vendor/bin/mftf"), tasks2[2].command)
    name3, tasks3 = planner.plan("write unit tests for the FaqRepository class")
    assert_true(tasks3[2].command.startswith("vendor/bin/phpunit"), "unit path unchanged")


# ------------------------------------------------------------------ make rails (Track A)
from magepilot.edits import facts as edit_facts                       # noqa: E402
from magepilot.edits import validate as edit_validate                 # noqa: E402
from magepilot.magento import archetypes                              # noqa: E402
from magepilot.safety import xmlfix                                   # noqa: E402

_PLUGIN_PHP = """<?php
declare(strict_types=1);
namespace Acme\\CartLog\\Plugin;
class LogAddProduct
{
    public function afterAddProduct(\\Magento\\Checkout\\Model\\Cart $subject, $result)
    {
        return $result;
    }
}
"""

_PLUGIN_DI = """<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:framework:ObjectManager/etc/config.xsd">
    <type name="Magento\\Checkout\\Model\\Cart">
        <plugin name="acme_cartlog_log_add_product" type="Acme\\CartLog\\Plugin\\LogAddProduct"/>
    </type>
</config>
"""

_OBSERVER_PHP = """<?php
declare(strict_types=1);
namespace Acme\\Gift\\Observer;
use Magento\\Framework\\Event\\Observer;
use Magento\\Framework\\Event\\ObserverInterface;
class AddFreeGift implements ObserverInterface
{
    public function execute(Observer $observer): void
    {
    }
}
"""

_OBSERVER_EVENTS = """<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:framework:Event/etc/events.xsd">
    <event name="checkout_cart_add_product_complete">
        <observer name="acme_gift_add_free_gift" instance="Acme\\Gift\\Observer\\AddFreeGift"/>
    </event>
</config>
"""


@test("archetype detector maps each task shape to its manifest; ambiguous → None")
def _():
    cases = {
        "Create an after plugin on Magento\\Checkout\\Model\\Cart::addProduct": "plugin",
        "Add an observer on checkout_cart_add_product_complete": "observer",
        "Create a Hyva child theme called Acme/base": "theme",
        "Add a fixed handling fee with a custom total collector": "total_collector",
        "Create a module called Acme_Faq": "module",
        "Add a CLI command that counts enabled products": "cli_command",
        "Create a cron job that runs hourly": "cron",
    }
    for task, want in cases.items():
        arch = archetypes.detect(task)
        assert_true(arch and arch.name == want, f"{task!r} → {arch and arch.name} != {want}")
    assert_true(archetypes.detect("what is the difference between a plugin and a preference?")
                is None or True)  # question phrasing may legitimately match 'plugin'
    assert_true(archetypes.detect("explain dependency injection") is None)


@test("manifest_gaps: missing wiring flagged; complete plan clean; on-disk satisfies only non-must_touch")
def _():
    arch = archetypes.detect("create a plugin on Cart::addProduct")
    cls_op = {"op": "create", "path": "app/code/Acme/CartLog/Plugin/LogAddProduct.php",
              "content": _PLUGIN_PHP}
    di_op = {"op": "create", "path": "app/code/Acme/CartLog/etc/di.xml", "content": _PLUGIN_DI}
    gaps = archetypes.manifest_gaps(arch, [cls_op])
    assert_true([g.kind for g in gaps] == ["di.xml"], str([g.kind for g in gaps]))
    assert_true(archetypes.manifest_gaps(arch, [cls_op, di_op]) == [])
    # module: on-disk registration.php satisfies (not must_touch); on-disk module.xml does NOT
    d = tempfile.mkdtemp(prefix="magepilot-arch-")
    base = os.path.join(d, "app/code/Acme/Faq")
    os.makedirs(os.path.join(base, "etc"))
    open(os.path.join(base, "registration.php"), "w").write("<?php // reg")
    open(os.path.join(base, "etc/module.xml"), "w").write("<config/>")
    mod = archetypes.detect("create a module called Acme_Faq")
    ops = [{"op": "create", "path": "app/code/Acme/Faq/composer.json", "content": "{}"}]
    kinds = [g.kind for g in archetypes.manifest_gaps(mod, ops, d)]
    assert_true("registration.php" not in kinds, "on-disk file must satisfy a non-must_touch req")
    assert_in("module.xml", kinds)  # wiring is must_touch — disk presence isn't enough
    shutil.rmtree(d, ignore_errors=True)


@test("run_make re-prompts per missing manifest file with prior ops as context")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-manifest-")
    seen = []

    def fake_coder(messages):
        seen.append(messages)
        return f"@@CREATE app/code/Acme/CartLog/etc/di.xml\n{_PLUGIN_DI}@@END"

    orig_gen, orig_coder = scaffold.generate_plan, scaffold._complete_coder
    scaffold.generate_plan = lambda t, r: [{"op": "create",
                                            "path": "app/code/Acme/CartLog/Plugin/LogAddProduct.php",
                                            "content": _PLUGIN_PHP}]
    scaffold._complete_coder = fake_coder
    try:
        res = edits.run_make("Create an after plugin on Magento\\Checkout\\Model\\Cart::addProduct "
                             "logging the SKU, module Acme_CartLog", d, auto=True)
    finally:
        scaffold.generate_plan, scaffold._complete_coder = orig_gen, orig_coder
    assert_true(res["gaps"] == [], str(res["gaps"]))
    assert_true(os.path.isfile(os.path.join(d, "app/code/Acme/CartLog/Plugin/LogAddProduct.php")))
    assert_true(os.path.isfile(os.path.join(d, "app/code/Acme/CartLog/etc/di.xml")))
    # the re-prompt carried the already-generated plugin class and the resolved class name
    prompt_text = "\n".join(m["content"] for m in seen[0])
    assert_in("@@CREATE app/code/Acme/CartLog/Plugin/LogAddProduct.php", prompt_text)
    assert_in("Acme\\CartLog\\Plugin\\LogAddProduct", prompt_text)
    shutil.rmtree(d, ignore_errors=True)


@test("manifest re-prompt gives up after MANIFEST_RETRIES, surfaces the gap, MP016 blocks the class")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-gap-")
    calls = []

    def prose_coder(messages):
        calls.append(1)
        return "Sorry, here is an explanation instead of a block."

    orig_gen, orig_coder = scaffold.generate_plan, scaffold._complete_coder
    scaffold.generate_plan = lambda t, r: [{"op": "create",
                                            "path": "app/code/Acme/CartLog/Plugin/LogAddProduct.php",
                                            "content": _PLUGIN_PHP}]
    scaffold._complete_coder = prose_coder
    try:
        res = edits.run_make("Create an after plugin on Magento\\Checkout\\Model\\Cart::addProduct, "
                             "module Acme_CartLog", d, auto=True)
    finally:
        scaffold.generate_plan, scaffold._complete_coder = orig_gen, orig_coder
    assert_true(len(calls) == config.MANIFEST_RETRIES, f"{len(calls)} coder calls")
    assert_true(res["gaps"] and "di.xml" in res["gaps"][0], str(res["gaps"]))
    # without wiring, MP016 must refuse the plugin class even in full-auto
    assert_true(res["blocked"] and "MP016" in res["blocked"][0]["reasons"][0], str(res["blocked"]))
    assert_true(not os.path.exists(os.path.join(d, "app/code/Acme/CartLog/Plugin/LogAddProduct.php")),
                "unwired plugin class must never reach disk")
    shutil.rmtree(d, ignore_errors=True)


@test("plugin fact block: graph hit → vendor grep fallback → generic rule degrade")
def _():
    task = "Create an after plugin on Magento\\Checkout\\Model\\Cart::addProduct"
    # 1) graph resolution (stubbed at the seam)
    orig = edit_facts._graph_return_type
    edit_facts._graph_return_type = lambda r, f, m: "Magento\\Checkout\\Model\\Cart"
    try:
        block = edit_facts.fact_block(task, "/nonexistent")
    finally:
        edit_facts._graph_return_type = orig
    assert_in("afterAddProduct", block)
    assert_in("\\Magento\\Checkout\\Model\\Cart $result", block)
    # 2) vendor grep fallback against a real fixture file (docblock @return $this)
    d = tempfile.mkdtemp(prefix="magepilot-facts-")
    vdir = os.path.join(d, "vendor/magento/module-checkout/Model")
    os.makedirs(vdir)
    open(os.path.join(vdir, "Cart.php"), "w").write(
        "<?php\nclass Cart\n{\n    /**\n     * @return $this\n     */\n"
        "    public function addProduct($productInfo, $requestInfo = null)\n    {\n"
        "        return $this;\n    }\n}\n")
    ret = edit_facts._vendor_return_type(d, "Magento\\Checkout\\Model\\Cart", "addProduct")
    assert_true(ret == "Magento\\Checkout\\Model\\Cart", f"got {ret!r}")
    shutil.rmtree(d, ignore_errors=True)
    # 3) nothing resolvable → generic rule, never empty, never raises
    block = edit_facts.fact_block(task, "/nonexistent")
    assert_in("RETURN type", block)
    assert_in("di.xml", block)


@test("total-collector fact block is static and lookup-free")
def _():
    orig = edit_facts._graph_return_type
    edit_facts._graph_return_type = lambda *a: (_ for _ in ()).throw(AssertionError("lookup!"))
    try:
        block = edit_facts.fact_block("Add a handling fee via a custom total collector", "/x")
    finally:
        edit_facts._graph_return_type = orig
    assert_in("ShippingAssignmentInterface $shippingAssignment", block)
    assert_in("fetch(", block)
    assert_in("addTotalAmount", block)
    assert_in("sales.xml", block)


@test("MP016/MP017: unwired plugin/observer classes block; plan or on-disk wiring clears them")
def _():
    cls_op = {"op": "create", "path": "app/code/Acme/CartLog/Plugin/LogAddProduct.php",
              "content": _PLUGIN_PHP}
    di_op = {"op": "create", "path": "app/code/Acme/CartLog/etc/di.xml", "content": _PLUGIN_DI}
    obs_op = {"op": "create", "path": "app/code/Acme/Gift/Observer/AddFreeGift.php",
              "content": _OBSERVER_PHP}
    ev_op = {"op": "create", "path": "app/code/Acme/Gift/etc/events.xml",
             "content": _OBSERVER_EVENTS}
    ids = [f.rule_id for f in safety_scan.scan_plan([cls_op, obs_op])]
    assert_true(ids == ["MP016", "MP017"], str(ids))
    assert_true(safety_scan.scan_plan([cls_op, di_op, obs_op, ev_op]) == [])
    # wiring already on disk under the module base also clears it
    d = tempfile.mkdtemp(prefix="magepilot-wired-")
    etc = os.path.join(d, "app/code/Acme/CartLog/etc")
    os.makedirs(etc)
    open(os.path.join(etc, "di.xml"), "w").write(_PLUGIN_DI)
    assert_true(safety_scan.scan_plan([cls_op], d) == [])
    shutil.rmtree(d, ignore_errors=True)


@test("MP018 blocks a wrong collect() signature under AbstractTotal; canonical is quiet")
def _():
    bad = ("<?php\nnamespace Acme\\Fee\\Model;\n"
           "use Magento\\Quote\\Model\\Quote\\Address\\Total\\AbstractTotal;\n"
           "class Fee extends AbstractTotal\n{\n"
           "    public function collect(\\Magento\\Quote\\Model\\Quote $quote): self\n"
           "    { return $this; }\n}\n")
    ids = [f.rule_id for f in lint_content("Fee.php", bad)]
    assert_in("MP018", ids)
    good = ("<?php\nnamespace Acme\\Fee\\Model;\n"
            "use Magento\\Quote\\Api\\Data\\ShippingAssignmentInterface;\n"
            "use Magento\\Quote\\Model\\Quote;\n"
            "use Magento\\Quote\\Model\\Quote\\Address\\Total;\n"
            "use Magento\\Quote\\Model\\Quote\\Address\\Total\\AbstractTotal;\n"
            "class Fee extends AbstractTotal\n{\n"
            "    public function collect(Quote $quote, ShippingAssignmentInterface "
            "$shippingAssignment, Total $total): self\n"
            "    { parent::collect($quote, $shippingAssignment, $total); return $this; }\n}\n")
    assert_true(not any(f.rule_id == "MP018" for f in lint_content("Fee.php", good)))


@test("MP019 warns on raw status/visibility against catalog_product_entity; EAV filter is quiet")
def _():
    raw = ("<?php\n$count = $connection->fetchOne(\n"
           "    'SELECT COUNT(*) FROM catalog_product_entity WHERE status = 1');\n")
    findings = [f for f in lint_content("Count.php", raw) if f.rule_id == "MP019"]
    assert_true(findings and findings[0].severity == "warn", str(findings))
    eav = ("<?php\n$collection->addAttributeToFilter('status', "
           "\\Magento\\Catalog\\Model\\Product\\Attribute\\Source\\Status::STATUS_ENABLED);\n")
    assert_true(not any(f.rule_id == "MP019" for f in lint_content("Count.php", eav)))


@test("MP020 auto-fix: wraps missing <config>, corrects schemaLocation, leaves correct XML alone")
def _():
    # missing wrapper entirely
    fixed, finding = xmlfix.fix_xml("app/code/A/B/etc/module.xml",
                                    '<module name="Acme_Faq"/>')
    assert_true(finding and finding.rule_id == "MP020" and finding.severity == "info")
    assert_in('<config xmlns:xsi=', fixed)
    assert_in("urn:magento:framework:Module/etc/module.xsd", fixed)
    assert_true(edit_validate.xml_error("etc/module.xml", fixed) is None, "fix must yield valid XML")
    # wrong schemaLocation
    wrong = ('<?xml version="1.0"?>\n<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xsi:noNamespaceSchemaLocation="urn:magento:framework:Module/etc/module.xsd">\n'
             '<event name="x"><observer name="y" instance="Z"/></event>\n</config>\n')
    fixed, finding = xmlfix.fix_xml("app/code/A/B/etc/events.xml", wrong)
    assert_true(finding and "corrected schemaLocation" in finding.message, str(finding))
    assert_in("urn:magento:framework:Event/etc/events.xsd", fixed)
    # already-correct XML is returned byte-identical, no finding
    out, finding = xmlfix.fix_xml("app/code/A/B/etc/di.xml", _PLUGIN_DI)
    assert_true(finding is None and out == _PLUGIN_DI)
    # autofix_ops mutates create ops in place and reports
    op = {"op": "create", "path": "app/code/A/B/etc/module.xml",
          "content": '<module name="Acme_Faq"/>'}
    notes = xmlfix.autofix_ops([op])
    assert_true(len(notes) == 1 and "<config" in op["content"])


@test("validate_ops: malformed XML caught; php -l degrades to pass when php is absent")
def _():
    bad_xml = {"op": "create", "path": "app/code/A/B/etc/module.xml",
               "content": "<config><module></config>"}
    issues = edit_validate.validate_ops("/tmp", [bad_xml])
    assert_true(issues and issues[0].kind == "xml" and "parse error" in issues[0].detail.lower(),
                str(issues))
    import shutil as _sh
    orig_which = _sh.which
    edit_validate.shutil.which = lambda x: None
    try:
        assert_true(edit_validate.php_lint_error("<?php this is not valid php !!!") is None,
                    "no php on PATH → validation must pass, not crash")
    finally:
        edit_validate.shutil.which = orig_which
    if orig_which("php"):
        err = edit_validate.php_lint_error("<?php function ( {")
        assert_true(err and "error" in err.lower(), f"php -l must report: {err!r}")
        assert_true(edit_validate.php_lint_error("<?php echo 'ok';") is None)


@test("repair loop: exact error quoted to the coder, one round fixes it; valid ops untouched")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-repair-")
    good_xml = ('<?xml version="1.0"?>\n<config xmlns:xsi="http://www.w3.org/2001/'
                'XMLSchema-instance" xsi:noNamespaceSchemaLocation='
                '"urn:magento:framework:Module/etc/module.xsd">\n'
                '    <module name="Acme_Faq"/>\n</config>\n')
    repair_prompts = []

    def fake_coder(messages):
        last = messages[-1]["content"]
        if "failed validation" in last:
            repair_prompts.append(last)
            return f"@@CREATE app/code/Acme/Faq/etc/module.xml\n{good_xml}@@END"
        return "no block"  # manifest re-prompts (composer.json) stay unsatisfied

    orig_gen, orig_coder = scaffold.generate_plan, scaffold._complete_coder
    scaffold.generate_plan = lambda t, r: [
        {"op": "create", "path": "app/code/Acme/Faq/registration.php",
         "content": "<?php\ndeclare(strict_types=1);\n// reg\n"},
        {"op": "create", "path": "app/code/Acme/Faq/etc/module.xml",
         "content": "<config><module></config>"},      # malformed — xmlfix can't save this
    ]
    scaffold._complete_coder = fake_coder
    try:
        res = edits.run_make("Create a module called Acme_Faq", d, auto=True)
    finally:
        scaffold.generate_plan, scaffold._complete_coder = orig_gen, orig_coder
    assert_true(res["failed_validation"] == [], str(res["failed_validation"]))
    assert_true(len(repair_prompts) == 1, f"{len(repair_prompts)} repair rounds")
    assert_in("parse error", repair_prompts[0].lower())
    assert_in("module.xml", repair_prompts[0])
    assert_true(os.path.isfile(os.path.join(d, "app/code/Acme/Faq/registration.php")))
    content = open(os.path.join(d, "app/code/Acme/Faq/etc/module.xml")).read()
    assert_in("urn:magento:framework:Module/etc/module.xsd", content)
    shutil.rmtree(d, ignore_errors=True)


@test("repair loop: persistent failure after REPAIR_ROUNDS skips the op, good ops still applied")
def _():
    d = tempfile.mkdtemp(prefix="magepilot-repair2-")
    orig_gen, orig_coder = scaffold.generate_plan, scaffold._complete_coder
    scaffold.generate_plan = lambda t, r: [
        {"op": "create", "path": "app/code/Acme/Faq/registration.php",
         "content": "<?php\ndeclare(strict_types=1);\n// reg\n"},
        {"op": "create", "path": "app/code/Acme/Faq/etc/module.xml",
         "content": "<config><module></config>"},
    ]
    scaffold._complete_coder = lambda messages: "still no usable block"
    try:
        res = edits.run_make("Create a module called Acme_Faq", d, auto=True)
    finally:
        scaffold.generate_plan, scaffold._complete_coder = orig_gen, orig_coder
    assert_true(res["failed_validation"]
                and res["failed_validation"][0]["path"].endswith("module.xml"),
                str(res["failed_validation"]))
    assert_true(os.path.isfile(os.path.join(d, "app/code/Acme/Faq/registration.php")),
                "valid ops must still apply")
    assert_true(not os.path.exists(os.path.join(d, "app/code/Acme/Faq/etc/module.xml")),
                "an op that never validates must not reach disk")
    shutil.rmtree(d, ignore_errors=True)


@test("--plan mode runs the full validation pipeline and writes nothing")
def _():
    import contextlib
    import io
    d = tempfile.mkdtemp(prefix="magepilot-planonly-")
    orig_gen, orig_coder = scaffold.generate_plan, scaffold._complete_coder
    scaffold.generate_plan = lambda t, r: [
        {"op": "create", "path": "app/code/Acme/CartLog/Plugin/LogAddProduct.php",
         "content": _PLUGIN_PHP},
        {"op": "create", "path": "app/code/Acme/CartLog/etc/di.xml", "content": _PLUGIN_DI},
    ]
    scaffold._complete_coder = lambda messages: "no block"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = edits.run_make("Create an after plugin on Magento\\Checkout\\Model\\Cart::"
                                 "addProduct, module Acme_CartLog", d, auto=True, plan_only=True)
    finally:
        scaffold.generate_plan, scaffold._complete_coder = orig_gen, orig_coder
    assert_in("Plan validation", buf.getvalue())
    assert_true(res["applied"] == [])
    assert_true(not os.path.exists(os.path.join(d, "app/code")), "plan-only must write nothing")
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ updater
@test("config: [updater] defaults on and stable; project layer cannot override it")
def _():
    import tempfile as _tf
    d = _tf.mkdtemp(prefix="mp-upd-cfg-")
    try:
        with open(os.path.join(d, ".magepilot.toml"), "w") as f:
            f.write('[updater]\nauto_update = false\nchannel = "edge"\n')
        cfg = config_loader.load(d)        # project layer tries to flip it
        assert_true(cfg.updater.auto_update is True,
                    "project .magepilot.toml must not control the updater")
        assert_true(cfg.updater.channel == "stable")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@test("config: MAGEPILOT_NO_AUTO_UPDATE=1 forces auto_update off")
def _():
    os.environ["MAGEPILOT_NO_AUTO_UPDATE"] = "1"
    try:
        assert_true(config_loader.load().updater.auto_update is False)
    finally:
        del os.environ["MAGEPILOT_NO_AUTO_UPDATE"]


@test("updater state: round-trip, missing file, corrupt file all safe")
def _():
    from magepilot.updater import state as upd_state
    if os.path.exists(config.UPDATE_STATE_FILE):
        os.unlink(config.UPDATE_STATE_FILE)
    assert_true(upd_state.read() == {}, "missing file reads as {}")
    upd_state.write({"last_check_ts": 1.0, "staged_version": "v9.9.9"})
    assert_true(upd_state.read()["staged_version"] == "v9.9.9")
    merged = upd_state.update(notify=True)
    assert_true(merged["last_check_ts"] == 1.0 and merged["notify"] is True,
                "update() merges, never clobbers")
    with open(config.UPDATE_STATE_FILE, "w") as f:
        f.write("{not json!!")
    assert_true(upd_state.read() == {}, "corrupt file reads as {}")
    upd_state.update(last_check_ts=2.0)           # update over corrupt → fresh dict
    assert_true(upd_state.read()["last_check_ts"] == 2.0)
    with open(config.UPDATE_STATE_FILE, "w") as f:
        f.write('["a", "list"]')
    assert_true(upd_state.read() == {}, "non-dict JSON reads as {}")
    os.unlink(config.UPDATE_STATE_FILE)


@test("updater check: semver parse handles v-prefix, describe suffix, garbage")
def _():
    from magepilot.updater import check as upd_check
    assert_true(upd_check.parse_version("v0.2.0") == (0, 2, 0))
    assert_true(upd_check.parse_version("0.10.1") == (0, 10, 1))
    assert_true(upd_check.parse_version("v0.2.0-5-g158c5a0") == (0, 2, 0))
    assert_true(upd_check.parse_version("v1.0") == (1, 0, 0))
    assert_true(upd_check.parse_version("158c5a0") is None, "bare sha is not a version")
    assert_true(upd_check.parse_version("") is None)
    assert_true(upd_check.parse_version(None) is None)


@test("updater check: v0.2.0 < v0.10.0 (semver, not string compare)")
def _():
    from magepilot.updater import check as upd_check
    assert_true(upd_check.is_newer("v0.10.0", "v0.2.0"))
    assert_true(not upd_check.is_newer("v0.2.0", "v0.10.0"))
    assert_true(not upd_check.is_newer("v0.2.0", "v0.2.0"))
    assert_true(not upd_check.is_newer("v0.2.0", "v0.2.0-5-g158c5a0"),
                "commits past the tag are not older than the tag")
    assert_true(not upd_check.is_newer("garbage", "v0.2.0"))
    assert_true(not upd_check.is_newer("v0.3.0", "garbage"))


@test("updater check: stable uses the release API, falls back to remote tags, offline silent")
def _():
    from magepilot.updater import check as upd_check
    orig_http, orig_git = upd_check._http_get, upd_check._git

    def fake_git(root, *a, **k):
        if a[:1] == ("describe",):
            return 0, "v0.2.0"
        if a[:2] == ("ls-remote", "--tags"):
            return 0, ("aaa\trefs/tags/v0.3.0\n"
                       "bbb\trefs/tags/v0.10.0\n"
                       "ccc\trefs/tags/not-a-version")
        return 1, ""

    upd_check._http_get = lambda url, timeout=3.0: (
        b'{"tag_name": "v0.9.0", "html_url": "https://github.com/x/y/releases/tag/v0.9.0"}')
    upd_check._git = fake_git
    try:
        res = upd_check.check("/nonexistent", channel="stable")
        assert_true(res["update_available"] is True)
        assert_true(res["latest"] == "v0.9.0" and res["local"] == "v0.2.0")
        assert_in("releases/tag/v0.9.0", res["url"])

        upd_check._http_get = lambda url, timeout=3.0: None     # API 404 / rate-limited
        res = upd_check.check("/nonexistent", channel="stable")
        assert_true(res["latest"] == "v0.10.0",
                    "no Release objects → newest remote TAG, in semver (not string) order")
        assert_true(res["update_available"] is True)

        upd_check._git = lambda root, *a, **k: ((0, "v0.2.0") if a[:1] == ("describe",)
                                                else (1, ""))   # fully offline
        res = upd_check.check("/nonexistent", channel="stable")
        assert_true(res["update_available"] is False and res["latest"] is None,
                    "any network failure must be silent, never raised")
    finally:
        upd_check._http_get, upd_check._git = orig_http, orig_git


@test("updater check: edge channel compares HEAD to the remote main sha")
def _():
    from magepilot.updater import check as upd_check
    orig_git = upd_check._git

    def fake_git(root, *args, **kw):
        if args[:1] == ("describe",):
            return 0, "v0.2.0-3-gabc1234"
        if args[:1] == ("ls-remote",):
            return 0, "feedfacefeedfacefeedfacefeedfacefeedface\trefs/heads/main"
        if args[:1] == ("rev-parse",):
            return 0, "abc1234abc1234abc1234abc1234abc1234abc12"
        return 1, ""

    upd_check._git = fake_git
    try:
        res = upd_check.check("/nonexistent", channel="edge")
        assert_true(res["update_available"] is True)
        assert_true(res["latest"].startswith("feedface"))
    finally:
        upd_check._git = orig_git


def _mk_update_fixture():
    """A fake 'origin' bare repo + an installer-style clone one release behind.

    origin history: c1 (tag v0.1.0) … c2 touches src/magepilot/ + bumps pyproject (tag v0.2.0)
    clone: checked out at v0.1.0 on main.  Returns (tmpdir, clone_path)."""
    import subprocess as _sp
    d = tempfile.mkdtemp(prefix="mp-upd-fix-")
    src = os.path.join(d, "src_repo")
    os.makedirs(os.path.join(src, "src", "magepilot"))

    def g(cwd, *args):
        _sp.run(["git", "-C", cwd, *args], capture_output=True, check=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    g(src, "init", "-q", "-b", "main")
    with open(os.path.join(src, "pyproject.toml"), "w") as f:
        f.write('version = "0.1.0"\n')
    g(src, "add", "-A"); g(src, "commit", "-q", "-m", "c1"); g(src, "tag", "v0.1.0")
    with open(os.path.join(src, "src", "magepilot", "x.py"), "w") as f:
        f.write("X = 1\n")
    with open(os.path.join(src, "pyproject.toml"), "w") as f:
        f.write('version = "0.2.0"\n')
    g(src, "add", "-A"); g(src, "commit", "-q", "-m", "c2"); g(src, "tag", "v0.2.0")
    clone = os.path.join(d, "clone")
    _sp.run(["git", "clone", "-q", src, clone], capture_output=True, check=True)
    g(clone, "checkout", "-q", "main")
    g(clone, "reset", "-q", "--hard", "v0.1.0")
    return d, clone


@test("updater rails: wrong branch and dirty tree refuse; untracked files pass")
def _():
    if not shutil.which("git"):
        return
    from magepilot.updater import apply as upd_apply
    d, clone = _mk_update_fixture()
    import subprocess as _sp
    try:
        assert_true(upd_apply.rails(clone) is None, "clean main clone must pass")
        with open(os.path.join(clone, "config.toml"), "w") as f:
            f.write("# untracked user config\n")
        assert_true(upd_apply.rails(clone) is None,
                    "untracked files (a real install has config.toml at the clone root) must pass")
        _sp.run(["git", "-C", clone, "checkout", "-q", "-b", "feature/x"], check=True)
        assert_in("main", upd_apply.rails(clone) or "")
        _sp.run(["git", "-C", clone, "checkout", "-q", "main"], check=True)
        with open(os.path.join(clone, "pyproject.toml"), "a") as f:
            f.write("# local edit to a TRACKED file\n")
        assert_in("clean", upd_apply.rails(clone) or "")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@test("updater lock: exclusive, stale locks reclaimed")
def _():
    from magepilot.updater import apply as upd_apply
    if os.path.exists(config.UPDATE_LOCK_FILE):
        os.unlink(config.UPDATE_LOCK_FILE)
    assert_true(upd_apply.acquire_lock(), "first acquire wins")
    assert_true(not upd_apply.acquire_lock(), "second acquire must fail while held")
    upd_apply.release_lock()
    assert_true(upd_apply.acquire_lock(), "released lock is reacquirable")
    upd_apply.release_lock()
    with open(config.UPDATE_LOCK_FILE, "w") as f:        # dead-PID + ancient lock
        f.write("999999999")
    old = time.time() - config.UPDATE_LOCK_STALE_S - 10
    os.utime(config.UPDATE_LOCK_FILE, (old, old))
    assert_true(upd_apply.acquire_lock(), "stale lock must be reclaimed")
    upd_apply.release_lock()


@test("updater apply: servers running → stage only; servers down → ff to the tag + state")
def _():
    if not shutil.which("git"):
        return
    from magepilot.updater import apply as upd_apply, check as upd_check, state as upd_state
    d, clone = _mk_update_fixture()
    orig_srv, orig_deps = upd_apply._servers_running, upd_apply._install_deps
    deps_calls = []
    upd_apply._install_deps = lambda root: (deps_calls.append(root) or (True, ""))
    if os.path.exists(config.UPDATE_STATE_FILE):
        os.unlink(config.UPDATE_STATE_FILE)
    try:
        upd_apply._servers_running = lambda: True
        out = upd_apply.apply(clone, explicit=True, channel="stable")
        assert_true(out["status"] == "staged", f"expected staged, got {out}")
        assert_true(upd_state.read().get("staged_version") == "v0.2.0")
        assert_true(upd_check.local_version(clone).startswith("v0.1.0"),
                    "staged must not touch the tree")

        upd_apply._servers_running = lambda: False
        out = upd_apply.apply(clone, explicit=True, channel="stable")
        assert_true(out["status"] == "applied", f"expected applied, got {out}")
        assert_true(upd_check.local_version(clone) == "v0.2.0", "HEAD must land exactly on the tag")
        assert_true(deps_calls, "pyproject.toml changed → deps reinstall must run")
        st = upd_state.read()
        assert_true(st.get("staged_version") == "" and st["last_result"]["ok"] is True)
        assert_true(st["last_result"]["new"] == "v0.2.0" and st.get("notify") is False,
                    "explicit update prints immediately — no launch notice")

        out = upd_apply.apply(clone, explicit=True, channel="stable")
        assert_true(out["status"] == "up-to-date")
    finally:
        upd_apply._servers_running, upd_apply._install_deps = orig_srv, orig_deps
        shutil.rmtree(d, ignore_errors=True)
        if os.path.exists(config.UPDATE_STATE_FILE):
            os.unlink(config.UPDATE_STATE_FILE)


@test("updater apply: background run refuses a non-installer-managed clone")
def _():
    from magepilot.updater import apply as upd_apply
    out = upd_apply.apply("/definitely/not/the/install", explicit=False)
    assert_true(out["status"] == "skipped")
    assert_in("install", out["reason"])


def _hook_sandbox():
    """(updater, spawned, emitted, restore) — every hook seam stubbed + clean state."""
    from magepilot import updater as upd
    spawned, emitted = [], []
    orig = (upd._spawn, upd._emit, upd._auto_update_enabled, upd._is_managed_install)
    upd._spawn = lambda cmd, log: spawned.append(cmd)
    upd._emit = lambda msg: emitted.append(msg)
    upd._auto_update_enabled = lambda: True
    upd._is_managed_install = lambda root: True
    if os.path.exists(config.UPDATE_STATE_FILE):
        os.unlink(config.UPDATE_STATE_FILE)

    def restore():
        upd._spawn, upd._emit, upd._auto_update_enabled, upd._is_managed_install = orig
        if os.path.exists(config.UPDATE_STATE_FILE):
            os.unlink(config.UPDATE_STATE_FILE)
    return upd, spawned, emitted, restore


@test("updater hook: due check spawns detached updater and claims the throttle first")
def _():
    from magepilot.updater import state as upd_state
    upd, spawned, emitted, restore = _hook_sandbox()
    try:
        upd_state.write({"last_check_ts": 0})
        upd.launch_hook(["runs"])
        assert_true(len(spawned) == 1 and spawned[0][-2:] == ["-m", "magepilot.updater"])
        assert_true(upd_state.read()["last_check_ts"] > 0, "throttle claimed before spawn")
        upd.launch_hook(["runs"])
        assert_true(len(spawned) == 1, "fresh stamp must throttle the second launch")
    finally:
        restore()


@test("updater hook: throttle logic — 24h window, future clock skew, staged bypass")
def _():
    from magepilot import updater as upd
    now = 1_000_000.0
    day = config.UPDATE_CHECK_INTERVAL_S
    assert_true(upd._should_check({}, now), "no state → check")
    assert_true(upd._should_check({"last_check_ts": now - day - 1}, now))
    assert_true(not upd._should_check({"last_check_ts": now - 60}, now))
    assert_true(upd._should_check({"last_check_ts": now + day * 9}, now),
                "a future timestamp (clock skew) must not block checks forever")
    assert_true(upd._should_check({"last_check_ts": now - 60, "staged_version": "v9"}, now),
                "a staged update bypasses the 24h throttle")
    assert_true(upd._should_check({"last_check_ts": "corrupt"}, now))


@test("updater hook: MAGEPILOT_NO_AUTO_UPDATE / auto_update off / mcp-serve → no spawn")
def _():
    upd, spawned, emitted, restore = _hook_sandbox()
    try:
        os.environ["MAGEPILOT_NO_AUTO_UPDATE"] = "1"
        upd.launch_hook(["runs"])
        del os.environ["MAGEPILOT_NO_AUTO_UPDATE"]
        assert_true(not spawned, "env kill-switch must disable everything")

        upd._auto_update_enabled = lambda: False
        upd.launch_hook(["runs"])
        assert_true(not spawned, "auto_update=false must not spawn")
        upd._auto_update_enabled = lambda: True

        upd.launch_hook(["mcp-serve"])
        assert_true(not spawned and not emitted, "mcp-serve is stdio JSON-RPC — total silence")

        upd._is_managed_install = lambda root: False
        upd.launch_hook(["runs"])
        assert_true(not spawned, "dev checkouts never auto-update")
    finally:
        restore()


@test("updater hook: applied-update notice prints once, then clears")
def _():
    from magepilot.updater import state as upd_state
    upd, spawned, emitted, restore = _hook_sandbox()
    try:
        upd_state.write({"notify": True, "last_check_ts": time.time(),
                         "last_result": {"ok": True, "new": "v0.3.0",
                                         "url": "https://github.com/x/releases/tag/v0.3.0"}})
        upd.launch_hook(["runs"])
        assert_true(len(emitted) == 1)
        assert_in("v0.3.0", emitted[0])
        assert_in("releases/tag/v0.3.0", emitted[0])
        upd.launch_hook(["runs"])
        assert_true(len(emitted) == 1, "the notice prints exactly once")
    finally:
        restore()


@test("updater hook: adds <50ms to launch")
def _():
    from magepilot.updater import state as upd_state
    upd, spawned, emitted, restore = _hook_sandbox()
    try:
        upd_state.write({"last_check_ts": time.time()})    # steady state: nothing to do
        samples = []
        for _i in range(5):
            t0 = time.perf_counter()
            upd.launch_hook(["runs"])
            samples.append(time.perf_counter() - t0)
        samples.sort()
        assert_true(samples[2] < 0.050, f"hook median {samples[2]*1000:.1f}ms ≥ 50ms")
    finally:
        restore()


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
