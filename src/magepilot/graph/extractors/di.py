"""di.xml extractor — the heart of the Magento graph. defusedxml (XXE-hardened — vendor XML is untrusted input): files are
small, child-axis paths suffice, zero new deps.

Captures: <preference for= type=> → PREFERS; <type><plugin/></type> → PLUGS_INTO
(sortOrder/disabled — entries WITHOUT type= still recorded: they disable plugins declared
elsewhere, which is exactly what "why is my plugin not firing" needs); <virtualType> →
VIRTUAL_TYPE_OF; object-typed <argument>s (recursing into arrays) → DI_ARGUMENT — this is
where checkout total collectors and pools are wired."""
import defusedxml.ElementTree as ET

_XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _clean(fqcn: str | None) -> str:
    return (fqcn or "").strip().lstrip("\\")


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    area = area or "global"

    for pref in root.findall("preference"):
        target, impl = _clean(pref.get("for")), _clean(pref.get("type"))
        if target and impl:
            store.add_edge("PREFERS", target, impl, file_id=file_id, area=area)

    for vt in root.findall("virtualType"):
        vname, vtype = _clean(vt.get("name")), _clean(vt.get("type"))
        if vname:
            store.add_node("virtual_type", vname.rsplit("\\", 1)[-1], vname,
                           file_id=file_id, module_id=module_id)
            if vtype:
                store.add_edge("VIRTUAL_TYPE_OF", vname, vtype, file_id=file_id, area=area)
        _arguments(store, vt, vname, file_id, area)

    for typ in root.findall("type"):
        tname = _clean(typ.get("name"))
        if not tname:
            continue
        for plugin in typ.findall("plugin"):
            pname = (plugin.get("name") or "").strip()
            ptype = _clean(plugin.get("type"))
            attrs = {"plugin_name": pname,
                     "sort_order": int(plugin.get("sortOrder") or 0),
                     "disabled": (plugin.get("disabled") or "").lower() == "true"}
            # no type= → a disable/override of a plugin declared elsewhere
            store.add_edge("PLUGS_INTO", ptype or f"(plugin:{pname})", tname,
                           file_id=file_id, area=area, attrs=attrs)
        _arguments(store, typ, tname, file_id, area)


def _arguments(store, type_el, owner: str, file_id: int, area: str) -> None:
    """<arguments><argument xsi:type="object">FQCN</argument> … recursing into arrays."""
    args = type_el.find("arguments")
    if args is None or not owner:
        return

    def visit(el, arg_name: str, key: str = ""):
        xsi = el.get(_XSI)
        if xsi == "object" and (el.text or "").strip():
            store.add_edge("DI_ARGUMENT", owner, _clean(el.text), file_id=file_id,
                           area=area, attrs={"arg": arg_name, **({"key": key} if key else {})})
        elif xsi == "array":
            for item in el.findall("item"):
                visit(item, arg_name, key=item.get("name") or "")

    for arg in args.findall("argument"):
        visit(arg, arg.get("name") or "")
