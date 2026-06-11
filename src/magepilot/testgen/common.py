"""Shared plumbing for the functional test generators.

Target resolution is graph-powered: an Alpine component name resolves to its template,
the template to the layout handle that renders it, and a three-part handle to the
storefront URL guess (faq_index_index → /faq/index/index). Every hop that can't be made
degrades explicitly — the caller sees WHAT was derived and what was guessed.
"""
import json
import re
from dataclasses import dataclass, field

from magepilot.edits.apply import _reverse_for, _save_journal, apply as _apply_op, preview
from magepilot.graph import queries
from magepilot.graph.store import get_graph
from magepilot.safety import scan as safety_scan

_HANDLE_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+){2}$")
_URLISH_RE = re.compile(r"^/[\w/.-]*$")


@dataclass
class StorefrontTarget:
    """Everything the generators need about one storefront thing-under-test."""
    name: str                       # slug for file naming
    url: str = ""                   # '/faq/index/index' (best-effort)
    handle: str = ""
    module: str = ""                # 'Vendor_Faq'
    alpine: str = ""                # component name, when resolved
    template: str = ""              # 'Vendor_Faq::faq/list.phtml'
    notes: list = field(default_factory=list)


def _url_from_handle(handle: str) -> str:
    """Three-part handles map to frontName/controller/action; anything else is unknown
    (cms pages, checkout_index_index works, catalog_product_view needs an entity id)."""
    if _HANDLE_RE.match(handle):
        return "/" + handle.replace("_", "/")
    return ""


def resolve_target(root: str, target: str) -> StorefrontTarget:
    """Alpine component | layout handle | template ref | /url/path → StorefrontTarget."""
    t = (target or "").strip()
    out = StorefrontTarget(name=re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_") or "page")

    if _URLISH_RE.match(t):                       # a plain URL — no graph needed
        out.url = t
        return out

    g = get_graph(root)
    if g is None:
        out.notes.append("knowledge graph not built — run `magepilot graph` for exact "
                         "selectors and URLs; falling back to the raw target")
        out.url = _url_from_handle(t)
        out.handle = t if out.url else ""
        return out
    try:
        # 1) Alpine component?
        comps = queries.alpine_components(g, t)
        comp = next((c for c in comps if c["component"] == t), None)
        if comp:
            out.alpine, out.template = comp["component"], comp["template"]
            out.name = re.sub(r"(?<!^)(?=[A-Z])", "_", comp["component"]).lower()
        # 2) template (from the component, or given directly)?
        tpl = out.template or (t if t.endswith(".phtml") else "")
        if tpl:
            out.template = tpl
            ctx = queries.template_context(g, tpl)
            if ctx["handles"]:
                out.handle = ctx["handles"][0]
            if ctx["blocks"]:
                out.module = ctx["blocks"][0]["block"].split("\\")[0] + "_" + \
                             ctx["blocks"][0]["block"].split("\\")[1]
        # 3) handle (from the template, or given directly)?
        if not out.handle and _HANDLE_RE.match(t):
            out.handle = t
        if out.handle:
            out.url = _url_from_handle(out.handle)
            if not out.module:
                row = g.db.execute(
                    "SELECT m.name FROM nodes n JOIN files f ON f.id=n.file_id "
                    "LEFT JOIN modules m ON m.id=f.module_id "
                    "WHERE n.kind='layout_handle' AND n.qname=?",
                    ("handle:" + out.handle,)).fetchone()
                if row and row["name"]:
                    out.module = row["name"]
        if out.template and not out.module and "::" in out.template:
            out.module = out.template.split("::")[0]
        if not out.url:
            out.notes.append(f"could not derive a URL from '{t}' — replace AMONPAGE_URL "
                             f"before running the test")
            out.url = "/AMONPAGE_URL"
    finally:
        g.close()
    return out


def write_ops(root: str, ops: list[dict], approver=None, auto: bool = False) -> dict:
    """Scan → preview → approve → apply a batch of CREATE ops as ONE undo journal.
    Returns {written: [...], skipped: [...], blocked: [...]}."""
    written, skipped, blocked, reverses = [], [], [], []
    approve_all = auto
    for op in ops:
        findings = safety_scan.scan_op(op)
        print(preview(root, op))
        for f in findings:
            print("   " + f.render())
        if safety_scan.blocked(findings):
            print("   ↳ ⛔ refused by policy\n")
            blocked.append({"path": op["path"],
                            "reasons": [f"{f.rule_id}: {f.message}"
                                        for f in safety_scan.blocked(findings)]})
            continue
        ok = approve_all
        if not ok and approver is not None:
            d = approver(op)
            if d == "all":
                approve_all = ok = True
            elif d == "yes":
                ok = True
        if not ok:
            print("   ↳ skipped\n")
            skipped.append(op["path"])
            continue
        rev = _reverse_for(root, op)
        msg = _apply_op(root, op)
        print("   ↳ " + msg + "\n")
        if msg.startswith(("skipped", "unknown")):
            skipped.append(op["path"])
        else:
            written.append(op["path"])
            reverses.append(rev)
    if written:
        _save_journal(root, reverses)
    return {"written": written, "skipped": skipped, "blocked": blocked}


def module_dir(module: str) -> str:
    v, _, m = module.partition("_")
    return f"app/code/{v}/{m}"


def graph_meta(root: str, target: StorefrontTarget) -> str:
    """One human line describing how much the graph could derive."""
    parts = []
    if target.alpine:
        parts.append(f"alpine={target.alpine}")
    if target.template:
        parts.append(f"template={target.template}")
    if target.handle:
        parts.append(f"handle={target.handle}")
    if target.module:
        parts.append(f"module={target.module}")
    return ", ".join(parts) or "no graph context"
