"""The hybrid `search` tool (docs/architecture/05, graph v2): one entry point that
routes internally so the model doesn't have to pick the right backend —

    FQCN / StudlyCaps / Class::method          → graph symbol lookup
    'GET /V1/…' / 'Query.field' / *.phtml      → graph wiring aspect
    structural keywords (plugin/observer/…)    → graph wiring on the named target
    regex metachars or a "quoted literal"      → ripgrep
    anything else (natural language)           → semantic ChromaDB search,
                                                  + a wiring footer for any FQCN
                                                  found in the top chunks

Results from different backends are concatenated with their source labeled — vector
scores and graph facts are never merged into one ranking (incomparable scales)."""
import re

from magepilot.graph import fmt, queries
from magepilot.graph.store import get_graph
from magepilot.tools.base import Param, RiskLevel, Tool, clip as _clip, root_tool
from magepilot.tools.fs import grep as _grep, search_code as _search_code

_FQCNISH = re.compile(r"^\\?(?:[A-Z]\w+\\)+[A-Z]\w+(?:::\w+)?$")
_STUDLY = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+(?:::\w+)?$")
_ROUTEISH = re.compile(r"^(?:GET|POST|PUT|DELETE|PATCH)?\s*/?V\d+/", re.I)
_GQLISH = re.compile(r"^(?:Query|Mutation)\.\w+$")
_EVENTISH = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}$")
_REGEXISH = re.compile(r"[\^\$\[\]\(\)\|\{\}\+\*\?]")
_STRUCTURAL = re.compile(
    r"\b(plugin|interceptor|observer|preference|resolver|route|endpoint|cron|consumer|"
    r"table|layout|handle|view ?model)s?\b", re.I)
_FQCN_IN_TEXT = re.compile(r"\b((?:[A-Z]\w+\\){2,}[A-Z]\w+)\b")


def search(root: str, query: str) -> str:
    """Route one query to the right backend(s)."""
    q = (query or "").strip().strip('"').strip("'")
    if not q:
        return "empty query"
    g = get_graph(root)
    try:
        # 1) unmistakable graph shapes
        if g is not None:
            if _ROUTEISH.match(q) or _GQLISH.match(q) or q.endswith(".phtml"):
                from magepilot.graph.tool import wiring
                from magepilot.tools.base import ToolContext
                return wiring(ToolContext(root=root), target=q)
            if _FQCNISH.match(q) or _STUDLY.match(q):
                bare = q.lstrip("\\").split("::")[0]
                hits = queries.find_symbol(g, bare, limit=6)
                if hits:
                    out = "[graph] " + fmt.refs(hits, f"'{bare}'")
                    if len(hits) == 1:
                        info = queries.class_info(g, hits[0].qname)
                        if info:
                            out = "[graph] " + fmt.class_info(info)
                    return _clip(out)
                # fall through to grep — the symbol may be outside the graph's scope
            if _EVENTISH.match(q):
                obs, disp = queries.observers_of(g, q)
                if obs or disp:
                    return "[graph] " + fmt.observers(obs, disp, q)
            if _STRUCTURAL.search(q):
                m = _FQCN_IN_TEXT.search(q)
                if m:
                    return ("[graph] "
                            + fmt.plugins(queries.plugins_for(g, m.group(1)), m.group(1)))
        # 2) regex-looking → exact text search
        if _REGEXISH.search(q) or (query.strip().startswith(('"', "'"))
                                   and query.strip().endswith(('"', "'"))):
            return "[grep] " + _grep(root, q)
        # 3) natural language → semantic, with a graph footer for any FQCN it surfaced
        sem = _search_code(root, q)
        out = "[semantic] " + sem
        if g is not None:
            seen = []
            for m in _FQCN_IN_TEXT.finditer(sem):
                if m.group(1) not in seen:
                    seen.append(m.group(1))
            for fqcn in seen[:2]:
                plugs = queries.plugins_for(g, fqcn)
                prefs = queries.preference_for(g, fqcn)
                if plugs or prefs:
                    out += (f"\n[graph] related wiring for {fqcn}: "
                            f"{len(plugs)} plugin(s), {len(prefs)} preference(s) — "
                            f"use `wiring` for details")
        return _clip(out)
    finally:
        if g is not None:
            g.close()


TOOLS = (
    Tool(
        name="search", fn=root_tool(search), primary="query", risk=RiskLevel.READ,
        params=(Param("query", required=True, description="symbol, route, event, regex, "
                                                          "or natural language"),),
        description="ONE search for everything — give it a class name, 'GET /V1/...', an "
                    "event name, a .phtml, a regex, or plain English; it picks the right "
                    "backend (knowledge graph / exact grep / semantic) and labels the "
                    "source. Good default when unsure which search tool fits.",
    ),
)
