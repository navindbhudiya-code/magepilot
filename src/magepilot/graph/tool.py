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


@_with_graph
def wiring(g, target: str, aspect: str = None, area: str = None) -> str:
    note = queries.partial_note(g)
    target = target.strip().lstrip("\\")
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
    return f"unknown aspect '{aspect}' — use plugins | preference | observers"


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
        params=(Param("target", required=True, description="FQCN or event name"),
                Param("aspect", description="plugins | preference | observers"),
                Param("area", description="frontend | adminhtml | webapi_rest | global")),
        description="EXACT Magento wiring facts from di.xml/events.xml: which plugins intercept "
                    "a class (with sortOrder/area/disabled), which preference rewrites it, which "
                    "observers listen to an event and where it is dispatched. Use BEFORE grep for "
                    "any 'what intercepts/listens/rewrites/handles' question. "
                    "Input: a FQCN or event name, or {\"target\": ..., \"aspect\": \"plugins\"}.",
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
