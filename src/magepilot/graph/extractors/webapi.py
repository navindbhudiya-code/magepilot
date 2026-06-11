"""webapi.xml extractor: <route url= method=><service class= method=/><resources/>
→ route node ('route:GET /V1/products/:sku') + ROUTES_TO (route → Class::method),
ACL resources in attrs."""
import defusedxml.ElementTree as ET


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    area = area or "global"
    for route in root.findall("route"):
        url = (route.get("url") or "").strip()
        http = (route.get("method") or "GET").strip().upper()
        svc = route.find("service")
        if not url or svc is None:
            continue
        cls = (svc.get("class") or "").strip().lstrip("\\")
        method = (svc.get("method") or "").strip()
        resources = [r.get("ref") for r in route.findall("resources/resource") if r.get("ref")]
        qname = f"route:{http} {url}"
        store.add_node("route", f"{http} {url}", qname, file_id=file_id,
                       module_id=module_id, attrs={"resources": resources})
        if cls and method:
            store.add_edge("ROUTES_TO", qname, f"{cls}::{method}", file_id=file_id,
                           area=area, attrs={"http": http, "resources": resources})
