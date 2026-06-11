"""Magento task archetypes — the deterministic task→required-file-set rail.

A `make` task that builds a plugin/observer/theme/… has a KNOWN minimum file set
(the class is useless without its wiring XML). The detector maps task text to an
archetype; `manifest_gaps` then reports which required files the generated plan is
missing so run_make can re-prompt the coder for each one individually. Pure
regex/path logic — works with zero LLM intelligence.

The detection regexes are also the planner's template matchers (agent/planner.py
imports them) so the two rails can never disagree about what a task is.
"""
import os
import re
from dataclasses import dataclass

# Shared "this builds something" verb prefix (planner templates reuse it).
CREATE = r"(?:create|add|generate|scaffold|build|set ?up|new|make|write)"

PLUGIN_RE = re.compile(CREATE + r"\b.*\bplugin\b|intercept\b", re.I)
OBSERVER_RE = re.compile(CREATE + r"\b.*\bobserver\b|observe .*event", re.I)
CRON_RE = re.compile(CREATE + r"\b.*\bcron\b", re.I)
THEME_RE = re.compile(CREATE + r"\b.*\btheme\b", re.I)
MODULE_RE = re.compile(CREATE + r"\b.*\bmodule\b", re.I)
TOTAL_COLLECTOR_RE = re.compile(
    r"\btotals?\s+collector\b|\bcustom\s+(?:order|cart|quote|sales)\s+total\b"
    r"|\bAbstractTotal\b|\bcollect\w*\s+totals?\b", re.I)
CLI_COMMAND_RE = re.compile(
    CREATE + r"\b.*\b(?:cli|console)\s+command\b|\bbin/magento\s+command\b", re.I)


@dataclass(frozen=True)
class Req:
    """One required file of an archetype's manifest.

    must_touch=True means the PLAN must create/edit it (new wiring is always needed:
    di.xml, events.xml, sales.xml, crontab.xml). False means a matching file already
    on disk under the module base satisfies it (registration.php, module.xml, …).
    """
    kind: str            # human label ("di.xml", "plugin class", …)
    path_re: str         # regex over op["path"] (relative, /-normalized)
    prompt: str          # coder re-prompt template, .format(**params)
    must_touch: bool = False
    on_disk: str = ""    # relative-to-base glob-free path used for the on-disk check


@dataclass(frozen=True)
class Archetype:
    name: str
    pattern: re.Pattern
    reqs: tuple


_DI_XML_RE = r"/etc/(?:(?:frontend|adminhtml|webapi_rest|webapi_soap|graphql)/)?di\.xml$"

# Ordered most-specific-first; first match wins. total_collector before plugin/module
# ("custom total collector module" must not match `module`); cli/cron before module too.
ARCHETYPES = (
    Archetype("total_collector", TOTAL_COLLECTOR_RE, (
        Req("collector class", r"/Model/.*\.php$",
            "Now emit ONLY the @@CREATE block for the total collector PHP class under "
            "{base}/Model/. It MUST extend \\Magento\\Quote\\Model\\Quote\\Address\\Total\\AbstractTotal."),
        Req("sales.xml", r"/etc/sales\.xml$",
            "Now emit ONLY the @@CREATE block for {base}/etc/sales.xml registering "
            "{php_class} in the quote totals section. Use the exact class name already shown.",
            must_touch=True, on_disk="etc/sales.xml"),
    )),
    Archetype("plugin", PLUGIN_RE, (
        Req("plugin class", r"/Plugin/.*\.php$",
            "Now emit ONLY the @@CREATE block for the plugin PHP class under {base}/Plugin/."),
        Req("di.xml", _DI_XML_RE,
            "Now emit ONLY the @@CREATE (or @@EDIT) block for {base}/etc/{area}di.xml "
            "declaring {php_class} as a plugin on {target} via "
            "<type name=\"...\"><plugin name=\"...\" type=\"...\"/></type>. "
            "Use the exact class name already shown.",
            must_touch=True),
    )),
    Archetype("observer", OBSERVER_RE, (
        Req("observer class", r"/Observer/.*\.php$",
            "Now emit ONLY the @@CREATE block for the observer PHP class under "
            "{base}/Observer/, implementing ObserverInterface."),
        Req("events.xml", r"/etc/(?:(?:frontend|adminhtml)/)?events\.xml$",
            "Now emit ONLY the @@CREATE block for {base}/etc/{area}events.xml wiring "
            "{php_class} to the event via <event name=\"...\"><observer name=\"...\" "
            "instance=\"...\"/></event>. Use the exact class name already shown.",
            must_touch=True),
    )),
    Archetype("cron", CRON_RE, (
        Req("cron class", r"/(?:Cron|Model)/.*\.php$",
            "Now emit ONLY the @@CREATE block for the cron job PHP class under {base}/Cron/."),
        Req("crontab.xml", r"/etc/crontab\.xml$",
            "Now emit ONLY the @@CREATE block for {base}/etc/crontab.xml scheduling "
            "{php_class} with a <group id=\"default\"><job .../></group>. Use the exact "
            "class name already shown.",
            must_touch=True, on_disk="etc/crontab.xml"),
    )),
    Archetype("cli_command", CLI_COMMAND_RE, (
        Req("command class", r"/(?:Console/)?Command/.*\.php$",
            "Now emit ONLY the @@CREATE block for the CLI command PHP class under "
            "{base}/Console/Command/, extending Symfony Command."),
        Req("di.xml", r"/etc/di\.xml$",
            "Now emit ONLY the @@CREATE (or @@EDIT) block for {base}/etc/di.xml registering "
            "{php_class} in Magento\\Framework\\Console\\CommandListInterface via "
            "<type name=\"Magento\\Framework\\Console\\CommandListInterface\"><arguments>"
            "<argument name=\"commands\" xsi:type=\"array\">…</argument></arguments></type>. "
            "Use the exact class name already shown.",
            must_touch=True),
    )),
    Archetype("theme", THEME_RE, (
        Req("registration.php", r"/registration\.php$",
            "Now emit ONLY the @@CREATE block for {base}/registration.php registering the THEME "
            "(ComponentRegistrar::THEME, 'frontend/{vendor}/{name}').",
            on_disk="registration.php"),
        Req("theme.xml", r"/theme\.xml$",
            "Now emit ONLY the @@CREATE block for {base}/theme.xml with the theme <title> and "
            "<parent>Hyva/default</parent>.",
            on_disk="theme.xml"),
        Req("composer.json", r"/composer\.json$",
            "Now emit ONLY the @@CREATE block for {base}/composer.json (type magento2-theme, "
            "registration.php in autoload files).",
            on_disk="composer.json"),
    )),
    Archetype("module", MODULE_RE, (
        Req("registration.php", r"/registration\.php$",
            "Now emit ONLY the @@CREATE block for {base}/registration.php "
            "(ComponentRegistrar::MODULE, '{vendor}_{name}').",
            on_disk="registration.php"),
        Req("module.xml", r"/etc/module\.xml$",
            "Now emit ONLY the @@CREATE block for {base}/etc/module.xml declaring "
            "{vendor}_{name} (no setup_version), wrapped in <config> with the module.xsd "
            "schemaLocation.",
            must_touch=True, on_disk="etc/module.xml"),
        Req("composer.json", r"/composer\.json$",
            "Now emit ONLY the @@CREATE block for {base}/composer.json (type magento2-module, "
            "registration.php in autoload files, psr-4 for {vendor}\\\\{name}\\\\).",
            on_disk="composer.json"),
    )),
)


def _extract(task: str):
    """Best-effort pull of (vendor, name) from a request so we don't ask when they're
    already given. (Moved from edits/scaffold.py — re-exported there.)"""
    vendor = name = None
    m = re.search(r"\b([A-Z][A-Za-z0-9]+)[\\_]([A-Z][A-Za-z0-9]+)\b", task)   # Vendor_Module / Vendor\Module
    if m:
        vendor, name = m.group(1), m.group(2)
    if not vendor:
        m = re.search(r"\bvendor[\s:_]+([A-Za-z][A-Za-z0-9]+)", task, re.I)
        if m:
            vendor = m.group(1)
    if not name:
        m = re.search(r"\b(?:name|named|called)[\s:]+([A-Za-z][A-Za-z0-9]+)", task, re.I)
        if m:
            name = m.group(1)
    return vendor, name


def detect(task: str):
    """First matching Archetype, or None when the task is ambiguous."""
    for arch in ARCHETYPES:
        if arch.pattern.search(task):
            return arch
    return None


def area_hint(task: str) -> str:
    """'frontend/' | 'adminhtml/' | '' (global) — the etc/ subdir the task implies."""
    low = task.lower()
    if re.search(r"\badmin(?:html)?\b|\bbackend\b", low):
        return "adminhtml/"
    if re.search(r"\bfrontend\b|\bstorefront\b", low):
        return "frontend/"
    return ""


def module_base(ops: list, task: str) -> str:
    """The module/theme base dir — ground truth from op paths first, task text fallback."""
    for op in ops:
        p = (op.get("path") or "").replace("\\", "/")
        m = re.match(r"(app/(?:code/[^/]+/[^/]+|design/frontend/[^/]+/[^/]+))/", p)
        if m:
            return m.group(1)
    vendor, name = _extract(task)
    if vendor and name:
        kind = "design/frontend" if THEME_RE.search(task) and not MODULE_RE.search(task) else "code"
        return f"app/{kind}/{vendor}/{name}"
    return ""


_NS_RE = re.compile(r"^\s*namespace\s+([\w\\]+)\s*;", re.M)
_CLASS_RE = re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+(\w+)", re.M)


def php_fqcn(content: str) -> str:
    """Vendor\\Module\\Plugin\\Foo from a PHP source, '' if undetectable."""
    ns = _NS_RE.search(content or "")
    cls = _CLASS_RE.search(content or "")
    if not cls:
        return ""
    return (ns.group(1) + "\\" if ns else "") + cls.group(1)


_TARGET_RE = re.compile(r"((?:\\?[A-Z]\w+){2,}(?:\\[A-Z]\w+)*)\s*::\s*(\w+)")


def plugin_target(task: str) -> tuple:
    """(fqcn, method) of the intercepted Class::method named in the task, or ('', '')."""
    m = _TARGET_RE.search(task.replace("\\\\", "\\"))
    if m:
        return m.group(1).lstrip("\\"), m.group(2)
    m = re.search(r"\b(?:on|of|for)\s+`?((?:\\?[A-Z]\w+\\)+[A-Z]\w+)`?", task.replace("\\\\", "\\"))
    if m:
        return m.group(1).lstrip("\\"), ""
    return "", ""


def params_for(arch, ops: list, task: str) -> dict:
    """The .format() params for Req re-prompts, derived from the plan + task."""
    base = module_base(ops, task)
    vendor, name = _extract(task)
    if not vendor and base:
        parts = base.split("/")
        if len(parts) >= 4:
            vendor, name = parts[-2], parts[-1]
    php_class = ""
    for op in ops:
        p = (op.get("path") or "").replace("\\", "/")
        if op.get("op") == "create" and p.endswith(".php") and "/etc/" not in p \
                and not p.endswith("registration.php"):
            php_class = php_fqcn(op.get("content", "")) or php_class
            if php_class:
                break
    target_cls, target_method = plugin_target(task)
    target = f"{target_cls}::{target_method}" if target_method else target_cls
    return {"base": base or "app/code/<Vendor>/<Module>",
            "vendor": vendor or "Vendor", "name": name or "Module",
            "area": area_hint(task), "php_class": php_class or "<the class above>",
            "target": target or "<the intercepted class>"}


def manifest_gaps(arch, ops: list, root: str = "") -> list:
    """The archetype's Reqs that the plan doesn't satisfy.

    A Req is satisfied when a create/edit op path matches its path_re — or, for
    non-must_touch Reqs, when the file already exists on disk under the module base.
    """
    base = module_base(ops, "")
    gaps = []
    for req in arch.reqs:
        rx = re.compile(req.path_re)
        hit = any(op.get("op") in ("create", "edit")
                  and rx.search("/" + (op.get("path") or "").replace("\\", "/").lstrip("/"))
                  for op in ops)
        if not hit and not req.must_touch and root and base and req.on_disk:
            hit = os.path.isfile(os.path.join(root, base, req.on_disk))
        if not hit:
            gaps.append(req)
    return gaps
