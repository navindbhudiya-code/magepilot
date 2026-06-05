"""Deterministic tests for the Magepilot agent — NO model required.

Covers the parts whose correctness must not depend on the LLM: the sandbox, every tool,
the CLI whitelist, the ReAct parser, and code indexing + semantic search on a fixture module.

    python -m agent.tests.run_tests
"""
import os
import shutil
import tempfile
import traceback

from agent import config

# Redirect the code index to a throwaway dir BEFORE the collection is opened.
_TMP = tempfile.mkdtemp(prefix="magepilot-test-")
config.CODE_CHROMA_PATH = os.path.join(_TMP, "code_index")
config.ROOT_MARKER = os.path.join(config.CODE_CHROMA_PATH, "root.txt")
config.UNDO_FILE = os.path.join(_TMP, "last_make.json")

from agent import codebase_index, tools, react_agent, actions, suggest, db, edits  # noqa: E402

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
    orig = edits.generate_plan
    edits.generate_plan = lambda task, root: plan          # no model in tests
    try:
        decisions = iter(["yes", "no"])
        res = edits.run_make("x", d, approver=lambda op: next(decisions))
    finally:
        edits.generate_plan = orig
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
    orig = edits.generate_plan
    edits.generate_plan = lambda task, root: plan
    try:
        edits.run_make("x", d, auto=True)
    finally:
        edits.generate_plan = orig
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
    orig = edits.generate_plan
    edits.generate_plan = lambda t, r: plan
    try:
        edits.run_make("x", d, auto=True)
    finally:
        edits.generate_plan = orig
    assert_true(os.path.isdir(os.path.join(d, "app/code/Vendor/Mod")))
    edits.undo(d)
    assert_true(not os.path.exists(os.path.join(d, "app/code/Vendor/Mod")), "empty created dir removed")
    assert_true(not os.path.exists(os.path.join(d, "app/code/Vendor")), "empty created dir removed")
    assert_true(os.path.isdir(os.path.join(d, "app/code")), "structural app/code must be KEPT")
    assert_true(os.path.isfile(os.path.join(d, "generated/code/Vendor/x.php")), "generated/ must be untouched")
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
