"""PHPUnit unit-test generation.

The skeleton is DETERMINISTIC and graph-powered: the class's constructor dependencies
come from the knowledge graph (`class_info` injections), each becomes a `createMock`,
and every public method gets a stub. The coder role optionally fills the stub bodies;
when the model is unreachable the skeleton alone is still a valid, lintable test with
one genuinely passing instantiation test.
"""
import os
import re

from magepilot.edits.apply import _reverse_for, _save_journal, apply as _apply_op, preview
from magepilot.graph.queries import class_info
from magepilot.graph.store import get_graph
from magepilot.llm.router import get_router
from magepilot.safety import scan as safety_scan

SKIP_METHODS = ("__construct", "__destruct", "setUp", "tearDown")
MAX_METHOD_STUBS = 6


def test_path(fqcn: str) -> str:
    """Vendor\\Faq\\Model\\FaqRepository → app/code/Vendor/Faq/Test/Unit/Model/FaqRepositoryTest.php"""
    parts = fqcn.lstrip("\\").split("\\")
    vendor, module, rest = parts[0], parts[1], parts[2:]
    return "app/code/{}/{}/Test/Unit/{}Test.php".format(vendor, module, "/".join(rest))


def _short(fqcn: str) -> str:
    return fqcn.rsplit("\\", 1)[-1]


def skeleton(root: str, fqcn: str) -> tuple[str, str]:
    """(relative test path, test file content). Constructor mocks from the graph."""
    fqcn = fqcn.strip().lstrip("\\")
    parts = fqcn.split("\\")
    if len(parts) < 3:
        raise ValueError(f"need a full class FQCN (Vendor\\Module\\...), got {fqcn!r}")
    injects, methods = [], []
    g = get_graph(root)
    if g is not None:
        try:
            info = class_info(g, fqcn)
            if info:
                injects = info.injects
                methods = [m.split("(")[0] for m in info.methods]
        finally:
            g.close()

    test_ns = "\\".join(parts[:2]) + "\\Test\\Unit\\" + "\\".join(parts[2:-1])
    test_ns = test_ns.rstrip("\\")
    subject = _short(fqcn)
    uses = sorted({fqcn, *(t for _, t in injects)})

    lines = ["<?php", "declare(strict_types=1);", "",
             f"namespace {test_ns};", "",
             "use PHPUnit\\Framework\\MockObject\\MockObject;",
             "use PHPUnit\\Framework\\TestCase;"]
    lines += [f"use {u};" for u in uses]
    lines += ["", f"class {subject}Test extends TestCase", "{",
              f"    private {subject} $subject;"]
    props = []
    for param, dep in injects:
        prop = (param or "$dep").lstrip("$")
        props.append((prop, _short(dep)))
        lines.append(f"    private {_short(dep)}|MockObject ${prop};")
    lines += ["", "    protected function setUp(): void", "    {"]
    for prop, dep in props:
        lines.append(f"        $this->{prop} = $this->createMock({dep}::class);")
    args = ", ".join(f"$this->{p}" for p, _ in props)
    lines += [f"        $this->subject = new {subject}({args});", "    }", "",
              "    public function testInstance(): void", "    {",
              f"        $this->assertInstanceOf({subject}::class, $this->subject);",
              "    }"]
    for m in [m for m in methods if m not in SKIP_METHODS
              and not m.startswith("_")][:MAX_METHOD_STUBS]:
        lines += ["", f"    public function test{m[0].upper()}{m[1:]}(): void", "    {",
                  f"        $this->markTestIncomplete('TODO: cover {subject}::{m}()');",
                  "    }"]
    lines += ["}", ""]
    return test_path(fqcn), "\n".join(lines)


def generate(root: str, fqcn: str, fill: bool = True) -> tuple[str, str]:
    """skeleton, with the coder role filling the TODO bodies when reachable. The model
    may only return the complete file; anything unusable falls back to the skeleton."""
    rel, content = skeleton(root, fqcn)
    if not fill:
        return rel, content
    try:
        out = get_router().complete("coder", [
            {"role": "system",
             "content": "You complete PHPUnit unit tests for Magento 2. Replace every "
                        "markTestIncomplete stub with a real arrange/act/assert test using "
                        "the mocks from setUp(). Keep namespace, uses, class name, setUp() "
                        "and testInstance() EXACTLY as given. Output ONLY the complete PHP "
                        "file, no fences, no prose."},
            {"role": "user", "content": content},
        ], stop=["<|im_end|>"], sampling={"max_tokens": 1400}, timeout=300)
        out = re.sub(r"^```(?:php)?|```$", "", out.strip(), flags=re.M).strip()
        # accept only a structurally plausible completion that ACTUALLY PARSES —
        # a 7B leaks prose into code often enough that php -l is the real gate
        if out.startswith("<?php") and f"class {_short(fqcn)}Test" in out \
                and "function setUp" in out and _php_lints(out):
            return rel, out + ("\n" if not out.endswith("\n") else "")
    except Exception:
        pass
    return rel, content


def _php_lints(code: str) -> bool:
    """php -l the candidate (True when php is unavailable — structural check stands)."""
    import shutil
    import subprocess
    import tempfile
    php = shutil.which("php")
    if not php:
        return True
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        r = subprocess.run([php, "-l", path], capture_output=True, timeout=20)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return True
    finally:
        os.unlink(path)


def write_test(root: str, fqcn: str, approver=None, auto: bool = False,
               fill: bool = True) -> dict:
    """Generate + preview + approve + write (scanned, journaled). Returns
    {path, written, findings}."""
    rel, content = generate(root, fqcn, fill=fill)
    op = {"op": "create", "path": rel, "content": content}
    findings = safety_scan.scan_op(op)
    print(preview(root, op))
    for f in findings:
        print("   " + f.render())
    if safety_scan.blocked(findings):
        print("   ↳ ⛔ refused by policy")
        return {"path": rel, "written": False, "findings": [f.render() for f in findings]}
    ok = auto or (approver is not None and approver(op) in ("yes", "all"))
    if not ok:
        print("   ↳ skipped")
        return {"path": rel, "written": False, "findings": []}
    rev = _reverse_for(root, op)
    print("   ↳ " + _apply_op(root, op))
    _save_journal(root, [rev])
    return {"path": rel, "written": True, "findings": []}
