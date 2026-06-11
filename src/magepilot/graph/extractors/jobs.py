"""Background-work extractors (one module for the small etc/ files):
crontab.xml → cron_job nodes + CRON_RUNS (job → Class::method, schedule in attrs)
queue_consumer.xml → consumer nodes + CONSUMES (consumer → handler Class::method)
indexer.xml → indexer nodes (class + view in attrs)
mview.xml → USES_TABLE edges from view subscriptions (what reindexes on which table)
"""
import defusedxml.ElementTree as ET


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    base = rel.rsplit("/", 1)[-1]
    if base == "crontab.xml":
        _crontab(store, root, file_id, module_id)
    elif base == "queue_consumer.xml":
        _consumers(store, root, file_id, module_id)
    elif base == "indexer.xml":
        _indexers(store, root, file_id, module_id)
    elif base == "mview.xml":
        _mview(store, root, file_id)


def _crontab(store, root, file_id, module_id) -> None:
    for group in root.findall("group"):
        gid = group.get("id") or "default"
        for job in group.findall("job"):
            name = (job.get("name") or "").strip()
            instance = (job.get("instance") or "").strip().lstrip("\\")
            method = (job.get("method") or "execute").strip()
            if not name:
                continue
            sched = job.get("schedule") or (job.findtext("schedule") or "").strip()
            store.add_node("cron_job", name, "cron:" + name, file_id=file_id,
                           module_id=module_id, attrs={"schedule": sched, "group": gid})
            if instance:
                store.add_edge("CRON_RUNS", "cron:" + name, f"{instance}::{method}",
                               file_id=file_id, attrs={"schedule": sched, "group": gid})


def _consumers(store, root, file_id, module_id) -> None:
    for c in root.findall("consumer"):
        name = (c.get("name") or "").strip()
        handler = (c.get("handler") or "").strip().lstrip("\\")
        if not name:
            continue
        store.add_node("consumer", name, "consumer:" + name, file_id=file_id,
                       module_id=module_id,
                       attrs={"queue": c.get("queue"), "connection": c.get("connection")})
        if handler:
            store.add_edge("CONSUMES", "consumer:" + name, handler.replace("::", "::"),
                           file_id=file_id, attrs={"queue": c.get("queue")})


def _indexers(store, root, file_id, module_id) -> None:
    for idx in root.findall("indexer"):
        iid = (idx.get("id") or "").strip()
        if not iid:
            continue
        store.add_node("indexer", iid, "indexer:" + iid, file_id=file_id,
                       module_id=module_id,
                       attrs={"class": (idx.get("class") or "").lstrip("\\"),
                              "view_id": idx.get("view_id")})


def _mview(store, root, file_id) -> None:
    for view in root.findall("view"):
        vid = (view.get("id") or "").strip()
        if not vid:
            continue
        store.ensure_node("indexer", "indexer:" + vid)
        for sub in view.findall("subscriptions/table"):
            t = (sub.get("name") or "").strip()
            if t:
                store.ensure_node("table", "table:" + t)
                store.add_edge("USES_TABLE", "indexer:" + vid, "table:" + t,
                               file_id=file_id, attrs={"via": "mview"})
