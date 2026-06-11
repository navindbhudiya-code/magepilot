"""Layout XML extractor: the request graph's render side.

view/<area>/layout/<handle>.xml → layout_handle node ('handle:<stem>');
<block class= [name=] [template=]> → HAS_BLOCK (handle → block class) + RENDERS
(block class → template ref); object-typed block arguments whose value looks like a
view model → ARG_VIEW_MODEL (block class → view-model class); <update handle=/> →
INCLUDES_HANDLE. referenceBlock template overrides are recorded as RENDERS too."""
import defusedxml.ElementTree as ET

_XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _walk(el):
    yield el
    for c in el:
        yield from _walk(c)


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    area = area or "global"
    handle = rel.rsplit("/", 1)[-1][:-len(".xml")]
    hq = "handle:" + handle
    store.add_node("layout_handle", handle, hq, file_id=file_id, module_id=module_id,
                   attrs={"area": area})

    for el in _walk(root):
        tag = el.tag.split("}")[-1]
        if tag == "update" and el.get("handle"):
            store.ensure_node("layout_handle", "handle:" + el.get("handle"))
            store.add_edge("INCLUDES_HANDLE", hq, "handle:" + el.get("handle"),
                           file_id=file_id, area=area)
            continue
        if tag not in ("block", "referenceBlock"):
            continue
        cls = (el.get("class") or "").strip().lstrip("\\")
        bname = (el.get("name") or "").strip()
        template = (el.get("template") or "").strip()
        subject = cls or (f"(block:{bname})" if bname else "")
        if not subject:
            continue
        if cls:
            store.add_edge("HAS_BLOCK", hq, cls, file_id=file_id, area=area,
                           attrs={"block_name": bname,
                                  "reference": tag == "referenceBlock"})
        if template:
            store.ensure_node("template", "tpl:" + template)
            store.add_edge("RENDERS", subject, "tpl:" + template, file_id=file_id,
                           area=area, attrs={"handle": handle, "block_name": bname})
        for arg in el.findall("arguments/argument"):
            if (arg.get(_XSI) or "") == "object" and (arg.text or "").strip():
                vm = arg.text.strip().lstrip("\\")
                store.add_edge("ARG_VIEW_MODEL", subject, vm, file_id=file_id, area=area,
                               attrs={"arg_name": arg.get("name") or "",
                                      "handle": handle, "block_name": bname})
