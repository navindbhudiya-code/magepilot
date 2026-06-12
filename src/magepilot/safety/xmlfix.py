"""MP020 — deterministic Magento config-XML repair. NEVER asks the model.

The fine-tune sometimes emits module.xml/events.xml/… without the <config> wrapper or
with a wrong/missing xsi:noNamespaceSchemaLocation. Both have exactly one correct
answer per file type, so a rule table + string surgery fixes them; the model's opinion
is irrelevant. Fixes mutate the op content in place before scan/preview and are
reported as INFO findings so the human sees what changed.
"""
import re

from magepilot.safety.lint_magento import INFO, Finding

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# basename → (root element, the one correct schemaLocation)
SCHEMAS = {
    "module.xml":  ("config", "urn:magento:framework:Module/etc/module.xsd"),
    "di.xml":      ("config", "urn:magento:framework:ObjectManager/etc/config.xsd"),
    "events.xml":  ("config", "urn:magento:framework:Event/etc/events.xsd"),
    "crontab.xml": ("config", "urn:magento:framework:Module/etc/crontab.xsd"),
    "sales.xml":   ("config", "urn:magento:module:Magento_Sales:etc/sales.xsd"),
    "webapi.xml":  ("routes", "urn:magento:module:Magento_Webapi:etc/webapi.xsd"),
}

_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>")
_SCHEMA_ATTR_RE = re.compile(r'xsi:noNamespaceSchemaLocation\s*=\s*"([^"]*)"')
_XSI_NS_RE = re.compile(r'xmlns:xsi\s*=\s*"[^"]*"')


def fix_xml(path: str, content: str) -> tuple[str, "Finding | None"]:
    """(possibly repaired content, the MP020 finding describing the repair or None)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name not in SCHEMAS:
        return content, None
    root_el, schema = SCHEMAS[name]
    fixed, repairs = content.strip(), []

    decl = _DECL_RE.match(fixed)
    body = fixed[decl.end():].strip() if decl else fixed
    decl_text = decl.group(0) if decl else '<?xml version="1.0"?>'
    if not decl:
        repairs.append("added XML declaration")

    open_re = re.compile(rf"<{root_el}\b[^>]*>")
    m = open_re.search(body)
    if not m:
        # no <config> (or <routes>) wrapper at all — wrap the whole body
        body = (f'<{root_el} xmlns:xsi="{XSI_NS}" '
                f'xsi:noNamespaceSchemaLocation="{schema}">\n{body}\n</{root_el}>')
        repairs.append(f"wrapped content in <{root_el}> with {schema.rsplit('/', 1)[-1]}")
    else:
        tag = m.group(0)
        new_tag = tag
        sm = _SCHEMA_ATTR_RE.search(new_tag)
        if sm and sm.group(1) != schema:
            new_tag = _SCHEMA_ATTR_RE.sub(f'xsi:noNamespaceSchemaLocation="{schema}"', new_tag)
            repairs.append(f"corrected schemaLocation to {schema}")
        elif not sm:
            attr = f' xsi:noNamespaceSchemaLocation="{schema}"'
            if new_tag.endswith("/>"):
                new_tag = new_tag[:-2].rstrip() + attr + "/>"
            else:
                new_tag = new_tag[:-1] + attr + ">"
            repairs.append(f"added schemaLocation {schema}")
        if "xsi:" in new_tag and not _XSI_NS_RE.search(new_tag):
            new_tag = new_tag.replace(f"<{root_el}", f'<{root_el} xmlns:xsi="{XSI_NS}"', 1)
            repairs.append("added xmlns:xsi")
        if new_tag != tag:
            body = body.replace(tag, new_tag, 1)

    if not repairs:
        return content, None
    fixed = decl_text + "\n" + body + "\n"
    return fixed, Finding("MP020", INFO, path, 1, "auto-fixed: " + "; ".join(repairs))


def autofix_ops(ops: list[dict]) -> list[Finding]:
    """Repair every create-op config XML in place. Returns the MP020 findings."""
    findings = []
    for op in ops:
        if op.get("op") != "create" or not (op.get("path") or "").endswith(".xml"):
            continue
        fixed, finding = fix_xml(op["path"], op.get("content", ""))
        if finding:
            op["content"] = fixed
            findings.append(finding)
    return findings
