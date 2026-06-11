"""Hyvä/Alpine extraction from .phtml templates — regex-based (no JS parser): the
patterns Hyvä actually uses are shallow and conventional.

  x-data="initProductSlider()"                  → alpine_component + DEFINES_ALPINE
  window/document.addEventListener('name', …)   → LISTENS_JS  (template → jsevent:name)
  window.dispatchEvent(new CustomEvent('name')) → EMITS_JS
  $dispatch('name', …)                          → EMITS_JS    (Alpine magic)

The template's qname matches the layout extractor's RENDERS targets
('tpl:Vendor_Module::path.phtml') when the file lives under a module's templates/,
so browser-side wiring joins the render graph.
"""
import re

_XDATA_RE = re.compile(r"""x-data\s*=\s*["']\s*(\w+)\s*\(""")
_LISTEN_RE = re.compile(r"""(?:window|document)\s*\.\s*addEventListener\s*\(\s*['"]([\w:.-]+)['"]""")
_EMIT_RE = re.compile(
    r"""(?:dispatchEvent\s*\(\s*new\s+CustomEvent\s*\(\s*['"]([\w:.-]+)['"]"""
    r"""|\$dispatch\s*\(\s*['"]([\w:.-]+)['"])""")
_TPL_SPLIT_RE = re.compile(r"(?:^|/)view/(?:frontend|adminhtml|base)/(?:templates|page_layout)/")


def template_qname(store, rel: str, module_id) -> str:
    """'tpl:Vendor_Module::path.phtml' when resolvable, else 'tpl:<rel>'."""
    m = _TPL_SPLIT_RE.search(rel)
    mod = store.module_qname(module_id)
    if m and mod:
        return "tpl:" + mod.removeprefix("module:") + "::" + rel[m.end():]
    return "tpl:" + rel


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    src = open(abs_path, encoding="utf-8", errors="replace").read()
    tpl = template_qname(store, rel, module_id)
    store.add_node("template", tpl.removeprefix("tpl:"), tpl, file_id=file_id,
                   module_id=module_id)

    def line_of(pos: int) -> int:
        return src.count("\n", 0, pos) + 1

    for m in _XDATA_RE.finditer(src):
        name = m.group(1)
        store.add_node("alpine_component", name, "alpine:" + name, file_id=file_id,
                       module_id=module_id, line_start=line_of(m.start()))
        store.add_edge("DEFINES_ALPINE", tpl, "alpine:" + name, file_id=file_id,
                       line=line_of(m.start()), area=area or "frontend")
    for m in _LISTEN_RE.finditer(src):
        store.ensure_node("js_event", "jsevent:" + m.group(1))
        store.add_edge("LISTENS_JS", tpl, "jsevent:" + m.group(1), file_id=file_id,
                       line=line_of(m.start()), area=area or "frontend")
    for m in _EMIT_RE.finditer(src):
        name = m.group(1) or m.group(2)
        store.ensure_node("js_event", "jsevent:" + name)
        store.add_edge("EMITS_JS", tpl, "jsevent:" + name, file_id=file_id,
                       line=line_of(m.start()), area=area or "frontend")
