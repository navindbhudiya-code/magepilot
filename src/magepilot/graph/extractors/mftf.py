"""MFTF extractor: Test/Mftf/Test/*.xml → mftf_test nodes + TESTS_MODULE edges.
MFTF tests reference pages/selectors rather than classes, so coverage is attributed at
the MODULE level — enough to answer 'does anything functional exercise this module?'."""
import defusedxml.ElementTree as ET


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    if root.tag.split("}")[-1] != "tests":
        return
    owner = store.module_qname(module_id)
    for test in root.findall("test"):
        name = (test.get("name") or "").strip()
        if not name:
            continue
        stories = ""
        ann = test.find("annotations")
        if ann is not None:
            stories = (ann.findtext("stories") or ann.findtext("title") or "").strip()
        store.add_node("mftf_test", name, "mftf:" + name, file_id=file_id,
                       module_id=module_id,
                       attrs={"stories": stories} if stories else None)
        if owner:
            store.add_edge("TESTS_MODULE", "mftf:" + name, owner, file_id=file_id)
