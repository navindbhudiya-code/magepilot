"""Pre-write scan pipeline (docs/architecture/07): every CREATE/EDIT operation passes
through (1) the path policy — vendor/, generated/, app/etc/env.php are write-BLOCKED
even in full-auto — and (2) the deterministic Magento/secret linter, BEFORE the approval
prompt, so the human sees findings next to the diff. BLOCK findings refuse the op and
go back to the model as observations."""
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
