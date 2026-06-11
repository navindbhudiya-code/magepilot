"""Extractor registry: classify a file path → (ftype, area), and dispatch extraction.

Area detection is path-rule ONLY (never inferred): etc/<area>/*.xml → that area,
etc/*.xml → global. Wrong-area answers are worse than grep, so the rule stays dumb.
"""
import re

from magepilot.graph.extractors import di, events, module, php

_AREA_RE = re.compile(
    r"(?:^|/)etc/(?:(adminhtml|frontend|webapi_rest|webapi_soap|graphql|crontab|cron)/)?[^/]+\.xml$")

AREAS = ("global", "frontend", "adminhtml", "webapi_rest", "webapi_soap", "graphql", "crontab")


def classify(rel: str) -> tuple[str | None, str | None]:
    """(ftype, area) for a path the graph cares about; (None, None) otherwise."""
    if rel.endswith("registration.php"):
        return "registration", None
    m = _AREA_RE.search(rel)
    if m:
        area = m.group(1) or "global"
        base = rel.rsplit("/", 1)[-1]
        if base == "di.xml":
            return "di", area
        if base == "events.xml":
            return "events", area
        if base == "module.xml" and area == "global":
            return "module", None
        return None, None
    if rel.endswith(".php"):
        return "php", None
    return None, None


EXTRACTORS = {
    "php": php.extract,
    "registration": lambda *a, **k: None,   # consumed by the module pass, not re-parsed
    "di": di.extract,
    "events": events.extract,
    "module": module.extract,
}
