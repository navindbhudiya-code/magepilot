"""Pre-write scan pipeline (docs/architecture/07): every CREATE/EDIT operation passes
through (1) the path policy — vendor/, generated/, app/etc/env.php are write-BLOCKED
even in full-auto — and (2) the deterministic Magento/secret linter, BEFORE the approval
prompt, so the human sees findings next to the diff. BLOCK findings refuse the op and
go back to the model as observations.

scan_plan adds the CROSS-FILE rules (MP016/MP017): a plugin/observer class is useless
without its di.xml/events.xml wiring, which a single-file linter can't see — so the
whole assembled plan is checked as one unit before the per-op loop."""
import glob as _glob
import os
import re

from magepilot.safety.lint_magento import BLOCK, Finding, lint_content

_PROTECTED_PATH = re.compile(r"^(?:vendor/|generated/|app/etc/env\.php$|app/etc/config\.php$)")


def path_findings(path: str) -> list[Finding]:
    p = path.replace("\\", "/").lstrip("./")
    if _PROTECTED_PATH.match(p):
        return [Finding("MP007", BLOCK, path, 0,
                        "writes to vendor/, generated/, or app/etc/{env,config}.php are refused",
                        "create your change in app/code/<Vendor>/<Module>/ instead")]
    return []


def scan_op(op: dict) -> list[Finding]:
    """Findings for one @@-block operation (create/edit/mkdir/delete)."""
    findings = path_findings(op.get("path", ""))
    if op.get("op") == "create":
        findings += lint_content(op["path"], op.get("content", ""))
    elif op.get("op") == "edit":
        # lint only the text being introduced — the existing file isn't this op's fault
        findings += [f for f in lint_content(op["path"], op.get("replace", ""))
                     if f.rule_id != "MP015"]       # a fragment can't carry declare()
    return findings


def blocked(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == BLOCK]


# ------------------------------------------------------------------ plan-level rules
_HOOK_RE = re.compile(r"public\s+function\s+(?:before|after|around)[A-Z]\w*\s*\(")
_OBSERVER_RE = re.compile(r"implements\s+[^{]*\bObserverInterface\b")
_NS_RE = re.compile(r"^\s*namespace\s+([\w\\]+)\s*;", re.M)
_CLASS_RE = re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+(\w+)", re.M)


def _fqcn(content: str) -> str:
    ns, cls = _NS_RE.search(content or ""), _CLASS_RE.search(content or "")
    if not cls:
        return ""
    return ((ns.group(1) + "\\") if ns else "") + cls.group(1)


def _plan_xml_text(ops: list[dict], basename: str) -> str:
    """Concatenated text of every matching XML the plan touches (create content + edit replace)."""
    parts = []
    for op in ops:
        p = (op.get("path") or "").replace("\\", "/")
        if p.endswith("/" + basename) or p.endswith(basename):
            parts.append(op.get("content", "") + "\n" + op.get("replace", ""))
    return "\n".join(parts)


def _disk_xml_text(root: str, ops: list[dict], basename: str) -> str:
    """Same-named XML already on disk under the module base(s) the plan touches."""
    if not root:
        return ""
    bases = set()
    for op in ops:
        m = re.match(r"(app/(?:code/[^/]+/[^/]+|design/frontend/[^/]+/[^/]+))/",
                     (op.get("path") or "").replace("\\", "/"))
        if m:
            bases.add(m.group(1))
    parts = []
    for base in bases:
        for f in _glob.glob(os.path.join(root, base, "etc", "**", basename), recursive=True):
            try:
                parts.append(open(f, encoding="utf-8", errors="replace").read())
            except OSError:
                pass
    return "\n".join(parts)


def _wired(fqcn: str, xml_text: str, attr: str) -> bool:
    """Is the class referenced as <... attr="FQCN"> (leading-backslash tolerant)?"""
    needle = re.escape(fqcn.lstrip("\\"))
    return bool(re.search(rf'{attr}\s*=\s*"\\?{needle}"', xml_text))


def scan_plan(ops: list[dict], root: str = "") -> list[Finding]:
    """Cross-file findings (MP016/MP017) over the WHOLE assembled plan.

    A PHP create op that defines plugin hook methods (before*/after*/around*) must be
    wired by a <plugin type="..."/> in a di.xml of the plan or already on disk; an
    ObserverInterface implementation likewise needs an <observer instance="..."/> in an
    events.xml. Findings land on the PHP op's path so the existing BLOCK semantics
    (refusal + model feedback) apply unchanged."""
    findings = []
    di_xml = _plan_xml_text(ops, "di.xml")
    ev_xml = _plan_xml_text(ops, "events.xml")
    for op in ops:
        p = (op.get("path") or "").replace("\\", "/")
        if op.get("op") != "create" or not p.endswith(".php") or "/etc/" in p:
            continue
        content = op.get("content", "")
        fqcn = _fqcn(content)
        if not fqcn:
            continue
        if _HOOK_RE.search(content) and not _OBSERVER_RE.search(content):
            wired = _wired(fqcn, di_xml, "type") \
                or _wired(fqcn, _disk_xml_text(root, ops, "di.xml"), "type")
            if not wired:
                findings.append(Finding(
                    "MP016", BLOCK, p, 1,
                    f"plugin class {fqcn} has no <plugin> entry in any di.xml of this change set",
                    "declare it: <type name=\"<intercepted class>\"><plugin name=\"...\" "
                    f"type=\"{fqcn}\"/></type> in etc/di.xml"))
        if _OBSERVER_RE.search(content):
            wired = _wired(fqcn, ev_xml, "instance") \
                or _wired(fqcn, _disk_xml_text(root, ops, "events.xml"), "instance")
            if not wired:
                findings.append(Finding(
                    "MP017", BLOCK, p, 1,
                    f"observer class {fqcn} has no <observer> entry in any events.xml of this change set",
                    "wire it: <event name=\"<event>\"><observer name=\"...\" "
                    f"instance=\"{fqcn}\"/></event> in etc/events.xml"))
    return findings
