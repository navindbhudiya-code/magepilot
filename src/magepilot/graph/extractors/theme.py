"""theme.xml extractor: theme node ('theme:Vendor/name' from its directory path) +
THEME_PARENT edge for the fallback chain."""
import defusedxml.ElementTree as ET
import re

_PATH_RE = re.compile(r"(?:app/design/(?:frontend|adminhtml)|design)/([^/]+/[^/]+)/theme\.xml$")


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    m = _PATH_RE.search(rel)
    name = m.group(1) if m else rel[:-len("/theme.xml")].rsplit("/", 2)[-2] + "/" + \
        rel[:-len("/theme.xml")].rsplit("/", 1)[-1]
    qname = "theme:" + name
    title = (root.findtext("title") or "").strip()
    store.add_node("theme", name, qname, file_id=file_id, module_id=module_id,
                   attrs={"title": title} if title else None)
    parent = (root.findtext("parent") or "").strip()
    if parent:
        store.ensure_node("theme", "theme:" + parent)
        store.add_edge("THEME_PARENT", qname, "theme:" + parent, file_id=file_id)
