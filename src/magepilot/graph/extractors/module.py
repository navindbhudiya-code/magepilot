"""module.xml extractor: the module node + <sequence> → DEPENDS_ON_MODULE edges.
(Module rows themselves are created in the build's module pass from registration.php;
this extractor adds the declaration metadata + dependency edges.)"""
import defusedxml.ElementTree as ET


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    mod = root.find("module")
    if mod is None:
        return
    name = (mod.get("name") or "").strip()
    if not name:
        return
    store.add_node("module", name, "module:" + name, file_id=file_id, module_id=module_id,
                   attrs={"setup_version": mod.get("setup_version")} if mod.get("setup_version") else None)
    seq = mod.find("sequence")
    if seq is not None:
        for dep in seq.findall("module"):
            dname = (dep.get("name") or "").strip()
            if dname:
                store.ensure_node("module", "module:" + dname)
                store.add_edge("DEPENDS_ON_MODULE", "module:" + name, "module:" + dname,
                               file_id=file_id, attrs={"source": "sequence"})
