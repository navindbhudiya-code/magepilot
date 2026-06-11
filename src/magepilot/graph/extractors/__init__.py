"""Extractor registry: classify a file path → (ftype, area), and dispatch extraction.

Area detection is path-rule ONLY (never inferred): etc/<area>/*.xml → that area,
etc/*.xml → global; layout files take the view/<area>/ they live in. Wrong-area answers
are worse than grep, so the rule stays dumb.
"""
import re

from magepilot.graph.extractors import (
    alpine, db_schema, di, events, graphqls, jobs, layout, mftf, module, php,
    theme, webapi,
)

_AREA_RE = re.compile(
    r"(?:^|/)etc/(?:(adminhtml|frontend|webapi_rest|webapi_soap|graphql|crontab|cron)/)?([^/]+\.xml)$")
_LAYOUT_RE = re.compile(r"(?:^|/)view/(frontend|adminhtml|base)/layout/[^/]+\.xml$")

AREAS = ("global", "frontend", "adminhtml", "webapi_rest", "webapi_soap", "graphql", "crontab")

_ETC_XML = {
    "di.xml": "di",
    "events.xml": "events",
    "webapi.xml": "webapi",
    "db_schema.xml": "db_schema",
    "crontab.xml": "jobs",
    "mview.xml": "jobs",
    "indexer.xml": "jobs",
    "queue_consumer.xml": "jobs",
}


_VIEW_AREA_RE = re.compile(r"(?:^|/)view/(frontend|adminhtml|base)/")


def classify(rel: str) -> tuple[str | None, str | None]:
    """(ftype, area) for a path the graph cares about; (None, None) otherwise."""
    if rel.endswith("registration.php"):
        return "registration", None
    if "Test/Mftf/" in rel and rel.endswith(".xml"):
        return "mftf", None
    if rel.endswith(".phtml"):
        m = _VIEW_AREA_RE.search(rel)
        area = m.group(1) if m else None
        return "phtml", (area if area and area != "base" else "frontend")
    m = _AREA_RE.search(rel)
    if m:
        area, base = m.group(1) or "global", m.group(2)
        if base == "module.xml" and area == "global":
            return "module", None
        ftype = _ETC_XML.get(base)
        return (ftype, area) if ftype else (None, None)
    m = _LAYOUT_RE.search(rel)
    if m:
        return "layout", m.group(1) if m.group(1) != "base" else "global"
    if rel.endswith("/theme.xml") and "/etc/" not in rel:
        return "theme", None
    if rel.endswith(".graphqls"):
        return "graphqls", "graphql"
    if rel.endswith(".php"):
        return "php", None
    return None, None


EXTRACTORS = {
    "php": php.extract,
    "registration": lambda *a, **k: None,   # consumed by the module pass, not re-parsed
    "di": di.extract,
    "events": events.extract,
    "module": module.extract,
    "webapi": webapi.extract,
    "db_schema": db_schema.extract,
    "jobs": jobs.extract,
    "layout": layout.extract,
    "theme": theme.extract,
    "graphqls": graphqls.extract,
    "phtml": alpine.extract,
    "mftf": mftf.extract,
}
