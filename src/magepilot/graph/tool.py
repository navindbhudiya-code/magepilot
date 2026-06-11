"""The 4 graph tools the agent sees (NOT 14 — a 7B picks reliably among few tools).
All READ tier. Descriptions are routing instructions: tool descriptions are a small
model's strongest routing mechanism."""
import re

from magepilot.graph import fmt, queries
from magepilot.graph.store import get_graph
from magepilot.tools.base import Param, RiskLevel, Tool, ToolContext

_NOT_BUILT = ("knowledge graph not built for this project — run `magepilot graph` "
              "first (then this tool answers wiring questions exactly)")
_EVENTISH = re.compile(r"^[a-z][a-z0-9_]+$")


def _with_graph(fn):
    def wrapped(ctx: ToolContext, **kw) -> str:
        g = get_graph(ctx.root)
        if g is None:
            return _NOT_BUILT
        try:
            return fn(g, **kw)
        finally:
            g.close()
    return wrapped


@_with_graph
def symbol(g, name: str, kind: str = None) -> str:
    note = queries.partial_note(g)
    hits = queries.find_symbol(g, name, kind=kind)
    if len(hits) == 1 and hits[0].kind in ("class", "interface", "trait", "virtual_type"):
        info = queries.class_info(g, hits[0].qname)
        if info:
            return fmt.class_info(info, note)
    return fmt.refs(hits, f"'{name}'") + (f"\n{note}" if note else "")


_ROUTEISH = re.compile(r"^(GET|POST|PUT|DELETE|PATCH)?\s*/?V\d+/", re.I)
_GQLISH = re.compile(r"^(Query|Mutation)\.\w+$")
_JSEVENTISH = re.compile(r"^[a-z][\w]*(?:-[\w]+)+$")   # kebab-case CustomEvent names


@_with_graph
def wiring(g, target: str, aspect: str = None, area: str = None) -> str:
    note = queries.partial_note(g)
    target = target.strip().lstrip("\\")
    # explicit aspects + cheap auto-detection for unmistakable shapes
    if aspect == "route" or (aspect is None and _ROUTEISH.match(target)):
        parts = target.split(None, 1)
        http, path = (parts if len(parts) == 2 else ("GET", parts[0]))
        return fmt.route(queries.what_handles_route(g, http, path), target, note)
    if aspect == "graphql" or (aspect is None and _GQLISH.match(target)):
        return fmt.gql(queries.graphql_resolver(g, target), target, note)
    if aspect == "template" or (aspect is None and target.endswith(".phtml")):
        return fmt.template(queries.template_context(g, target), note)
    if aspect == "table":
        return fmt.table(queries.table_info(g, target), target, note)
    if aspect == "callers":
        return fmt.calls(queries.callers_of(g, target), target, "callers", note)
    if aspect == "callees":
        return fmt.calls(queries.callees_of(g, target), target, "callees", note)
    if aspect == "tests":
        return fmt.tests(queries.tests_for(g, target), target, note)
    if aspect in ("jsevent", "js") or (aspect is None and "-" in target
                                       and _JSEVENTISH.match(target)):
        return fmt.js_event(queries.js_event_info(g, target), note)
    if aspect == "alpine":
        return fmt.alpine(queries.alpine_components(g, target if target != "*" else ""),
                          target, note)
    if aspect in ("observers", "events") or (aspect is None and _EVENTISH.match(target)):
        obs, disp = queries.observers_of(g, target, area=area)
        return fmt.observers(obs, disp, target, note)
    if aspect == "preference":
        return fmt.preferences(queries.preference_for(g, target, area=area), target, note)
    if aspect == "plugins" or aspect is None:
        out = fmt.plugins(queries.plugins_for(g, target, area=area), target)
        prefs = queries.preference_for(g, target, area=area)
        if prefs:
            out += "\n" + fmt.preferences(prefs, target)
        return out + (f"\n{note}" if note else "")
    return ("unknown aspect '" + aspect + "' — use plugins | preference | observers | "
            "route | graphql | template | table | callers | callees | tests | "
            "jsevent | alpine")


@_with_graph
def impact(g, fqcn: str) -> str:
    return fmt.impact(queries.impact_of(g, fqcn), queries.partial_note(g))


@_with_graph
def diagnose_plugin(g, plugin: str) -> str:
    return fmt.diagnosis(queries.diagnose_plugin(g, plugin), queries.partial_note(g))


TOOLS = (
    Tool(
        name="symbol", fn=symbol, primary="name", risk=RiskLevel.READ,
        params=(Param("name", required=True, description="class/interface/event/table name or FQCN"),
                Param("kind", description="filter: class|interface|trait|method|event|table")),
        description="Look up a class/interface/event by name in the knowledge graph — exact "
                    "locations + extends/implements + constructor dependencies + wiring counts. "
                    "Use BEFORE grep when you know a symbol's name (it indexes vendor/ too).",
    ),
    Tool(
        name="wiring", fn=wiring, primary="target", risk=RiskLevel.READ,
        params=(Param("target", required=True,
                      description="FQCN, event name, 'GET /V1/...', 'Query.field', x.phtml, or table"),
                Param("aspect", description="plugins | preference | observers | route | "
                                            "graphql | template | table | callers | "
                                            "callees | tests | jsevent | alpine"),
                Param("area", description="frontend | adminhtml | webapi_rest | global")),
        description="EXACT Magento wiring facts from the config graph: plugins on a class "
                    "(sortOrder/area/disabled), the preference that rewrites it, observers of an "
                    "event + dispatch sites, which service handles a REST route ('GET /V1/...'), "
                    "the resolver behind 'Query.field', which blocks/view-models render a .phtml, "
                    "and a table's owner/columns/foreign keys. Use BEFORE grep for any 'what "
                    "intercepts/listens/handles/renders' question.",
    ),
    Tool(
        name="impact", fn=impact, primary="fqcn", risk=RiskLevel.READ,
        params=(Param("fqcn", required=True, description="class or interface FQCN"),),
        description="Blast radius of changing a class/interface: implementors, subclasses, "
                    "constructor injectors, plugins, preferences, DI arguments — exact counts "
                    "with examples. Use before proposing a change to a shared class.",
    ),
    Tool(
        name="diagnose_plugin", fn=diagnose_plugin, primary="plugin", risk=RiskLevel.READ,
        params=(Param("plugin", required=True, description="the plugin class FQCN"),),
        description="Why is my plugin not firing? Checks: declared at all, area mismatch, "
                    "disabled (here or by ANOTHER module), before/after/around method-name "
                    "typos vs the target's real methods, preference shadowing, module disabled.",
    ),
)
