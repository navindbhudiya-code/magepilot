"""events.xml extractor: <event name><observer name= instance= [disabled] [shared]/>
→ event node + OBSERVES edges (observer class → event), area-tagged."""
import defusedxml.ElementTree as ET


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    area = area or "global"
    for event in root.findall("event"):
        ename = (event.get("name") or "").strip()
        if not ename:
            continue
        store.add_node("event", ename, ename, file_id=file_id)
        for obs in event.findall("observer"):
            instance = (obs.get("instance") or "").strip().lstrip("\\")
            attrs = {"observer_name": (obs.get("name") or "").strip(),
                     "disabled": (obs.get("disabled") or "").lower() == "true",
                     "shared": (obs.get("shared") or "true").lower() != "false"}
            store.add_edge("OBSERVES", instance or f"(observer:{attrs['observer_name']})",
                           ename, file_id=file_id, area=area, attrs=attrs)
