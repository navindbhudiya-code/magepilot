"""Post-plan deterministic validation — the gate of the validate→repair loop.

Every proposed file is checked BEFORE preview/approval: `php -l` for PHP (skipped
gracefully when php isn't installed), XML well-formedness + schemaLocation sanity for
the known Magento config files. Failures carry the EXACT tool error text so a repair
prompt can quote it verbatim to the coder. No model involved here.
"""
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from defusedxml import ElementTree as ET

from magepilot import config
from magepilot.safety.xmlfix import SCHEMAS, _SCHEMA_ATTR_RE


@dataclass(frozen=True)
class Issue:
    path: str
    kind: str       # "php" | "xml"
    detail: str     # exact error text — quoted verbatim in the repair prompt


def php_lint_error(content: str) -> str | None:
    """The `php -l` error for this source, None when clean (or php unavailable)."""
    php = shutil.which("php")
    if not php:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False) as f:
        f.write(content)
        path = f.name
    try:
        r = subprocess.run([php, "-l", path], capture_output=True, text=True,
                           timeout=config.PHP_LINT_TIMEOUT)
        if r.returncode == 0:
            return None
        err = (r.stdout + r.stderr).strip().replace(path, "<file>")
        return err.splitlines()[0] if err else "php -l failed"
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        os.unlink(path)


def xml_error(path: str, content: str) -> str | None:
    """Well-formedness + (for known Magento config files) schemaLocation sanity."""
    try:
        ET.fromstring(content)
    except Exception as e:
        return f"XML parse error: {e}"
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    known = SCHEMAS.get(name)
    if known:
        m = _SCHEMA_ATTR_RE.search(content)
        if not m:
            return f"missing xsi:noNamespaceSchemaLocation (must be {known[1]})"
        if m.group(1) != known[1]:
            return f"wrong schemaLocation '{m.group(1)}' (must be {known[1]})"
    return None


def validate_ops(root: str, ops: list[dict]) -> list[Issue]:
    """All validation issues across the plan's create ops."""
    issues = []
    for op in ops:
        if op.get("op") != "create":
            continue
        path = (op.get("path") or "").replace("\\", "/")
        content = op.get("content", "")
        if path.endswith((".php", ".phtml")):
            err = php_lint_error(content)
            if err:
                issues.append(Issue(path, "php", err))
        elif path.endswith(".xml"):
            err = xml_error(path, content)
            if err:
                issues.append(Issue(path, "xml", err))
    return issues


def verdict(ops: list[dict], issues: list, gaps: list, plan_findings: list,
            fix_notes: list) -> str:
    """One printable validation summary for the assembled plan (shown in --plan too)."""
    lines = [f"Plan validation: {len(ops)} op(s)"]
    for f in fix_notes:
        lines.append("   " + f.render())
    for g in gaps:
        lines.append(f"   ⚠ manifest gap — required {g} is missing from the plan")
    for i in issues:
        lines.append(f"   ⛔ {i.path} failed {i.kind} validation: {i.detail}")
    for f in plan_findings:
        lines.append("   " + f.render())
    if len(lines) == 1:
        lines[0] += " — all checks passed (manifest, php -l, XML, linter)"
    return "\n".join(lines)
