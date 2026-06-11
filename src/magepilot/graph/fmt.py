"""Observation formatters — counts before lists, vendor collapsed, one trailing `note:`
for the highest-value caveat, hard-capped at the tool observation limit. These strings
are what the 7B model reads; density is the contract."""
from magepilot.tools.base import clip as _clip


def refs(items, header: str) -> str:
    if not items:
        return f"no symbols match {header}"
    lines = [f"{len(items)} symbol(s) for {header}:"]
    for r in items:
        loc = f"{r.file}:{r.line}" if r.file else "(declared in config only)"
        v = " [vendor]" if r.vendor else ""
        lines.append(f"- {r.kind} {r.qname}  {loc}{v}"
                     + (f"  module={r.module}" if r.module else ""))
    return _clip("\n".join(lines))


def class_info(info, note: str = "") -> str:
    r = info.ref
    lines = [f"{r.kind} {r.qname}  ({r.file}:{r.line}"
             + (f", module {r.module}" if r.module else "") + ")"]
    if info.extends:
        lines.append("extends: " + ", ".join(info.extends))
    if info.implements:
        lines.append("implements: " + ", ".join(info.implements))
    if info.traits:
        lines.append("uses traits: " + ", ".join(info.traits))
    if info.injects:
        lines.append("constructor injects:")
        lines += [f"  {p or '$?'}: {t}" for p, t in info.injects[:10]]
    if info.methods:
        lines.append(f"methods ({len(info.methods)} shown): " + ", ".join(
            m.split("(")[0] for m in info.methods))
    if info.preferred_to:
        lines.append("⚠ PREFERENCE: replaced by " + ", ".join(info.preferred_to))
    c = info.counts
    wired = ", ".join(f"{k}={v}" for k, v in c.items() if v)
    if wired:
        lines.append(f"wiring: {wired}  (use the `wiring`/`impact` tools for details)")
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def plugins(items, target: str, note: str = "") -> str:
    if not items:
        return f"no plugins intercept {target}" + (f"\n{note}" if note else "")
    lines = [f"{len(items)} plugin(s) on {target} (or its parents/interfaces):"]
    for i, p in enumerate(items, 1):
        mtxt = ""
        if p.methods:
            parts = [f"{t}:{m}" + ("" if has else " ⚠NO-SUCH-METHOD") for t, m, has in p.methods]
            mtxt = "  [" + ", ".join(parts[:4]) + "]"
        lines.append(f"{i}. {p.plugin_fqcn}  sortOrder={p.sort_order} [{p.area}]"
                     + ("  DISABLED" if p.disabled else "")
                     + f"  {p.declared_in}{mtxt}")
        if p.target != target:
            lines.append(f"   (declared on parent/interface {p.target})")
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def preferences(prefs, target: str, note: str = "") -> str:
    if not prefs:
        return f"no preference rewrites {target}" + (f"\n{note}" if note else "")
    lines = [f"{len(prefs)} preference(s) for {target}:"]
    for p in prefs:
        lines.append(f"- -> {p['impl']} [{p['area']}] {p['declared_in']}"
                     + ("  ← WINNER" if p.get("winner") else "")
                     + ("  [vendor]" if p.get("vendor") else ""))
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def observers(obs, dispatchers, event: str, note: str = "") -> str:
    lines = []
    if obs:
        lines.append(f"{len(obs)} observer(s) of '{event}':")
        for o in obs:
            lines.append(f"- {o.observer_fqcn}  name={o.observer_name} [{o.area}]"
                         + ("  DISABLED" if o.disabled else "") + f"  {o.declared_in}")
    else:
        lines.append(f"no observers registered for '{event}'")
    if dispatchers:
        lines.append(f"dispatched from {len(dispatchers)} site(s):")
        lines += [f"- {d}" for d in dispatchers[:6]]
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def impact(report: dict, note: str = "") -> str:
    rels = report.get("relations", {})
    if not rels:
        return (f"nothing in the graph depends on {report['target']} "
                "(or it is not indexed)" + (f"\n{note}" if note else ""))
    total = sum(r["count"] for r in rels.values())
    lines = [f"impact of changing {report['target']}: {total} dependent wiring(s)"]
    for label, r in rels.items():
        lines.append(f"- {label}: {r['count']}")
        lines += [f"    {e}" for e in r["examples"]]
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def route(info: dict | None, asked: str, note: str = "") -> str:
    if info is None:
        return f"no webapi route matches {asked}" + (f"\n{note}" if note else "")
    lines = [f"route {info['route']}  ({info['declared_in']})",
             f"service: {info['service']}"]
    if info.get("impl"):
        lines.append(f"implementation (via preference): {info['impl']}")
    if info.get("resources"):
        lines.append("ACL: " + ", ".join(info["resources"]))
    if info.get("plugins"):
        lines.append(f"{info['plugins']} plugin(s) intercept the implementation — "
                     f"use `wiring` for the list")
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def gql(info: dict | None, asked: str, note: str = "") -> str:
    if info is None:
        return f"no GraphQL resolver found for {asked}" + (f"\n{note}" if note else "")
    lines = [f"{info['field']} resolves via {info['resolver']}",
             f"declared: {info['declared_in']}"]
    if info.get("resolver_file"):
        lines.append(f"resolver class: {info['resolver_file']}")
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def template(ctx: dict, note: str = "") -> str:
    if not ctx["blocks"]:
        return (f"no layout wiring renders {ctx['template']} "
                "(theme overrides and PHP-side setTemplate are not indexed yet)"
                + (f"\n{note}" if note else ""))
    lines = [f"{ctx['template']} is rendered by {len(ctx['blocks'])} block wiring(s):"]
    for b in ctx["blocks"]:
        lines.append(f"- {b['block']}  handle={b['handle']} [{b['area']}]  {b['declared_in']}")
    if ctx["view_models"]:
        lines.append("view models passed in:")
        lines += [f"- {v['class']}  (argument '{v['arg']}')" for v in ctx["view_models"]]
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def table(info: dict | None, asked: str, note: str = "") -> str:
    if info is None:
        return f"table '{asked}' is not in the graph (no db_schema.xml declares it)" \
               + (f"\n{note}" if note else "")
    lines = [f"table {info['table']}  engine={info['engine']}  "
             f"{info['n_columns']} column(s)"
             + (f"  owner={info['owner']}" if info["owner"] else "")]
    if info["columns"]:
        lines.append("columns: " + ", ".join(info["columns"][:24])
                     + (" …" if info["n_columns"] > 24 else ""))
    if info.get("extended_by"):
        lines.append("extended by: " + ", ".join(info["extended_by"]))
    if info["fks_out"]:
        lines.append("references: " + ", ".join(f"{t} (via {c})" for t, c in info["fks_out"]))
    if info["fks_in"]:
        lines.append("referenced by: " + ", ".join(info["fks_in"]))
    if info["used_by"]:
        lines.append("used by: " + ", ".join(info["used_by"]))
    if note:
        lines.append(note)
    return _clip("\n".join(lines))


def diagnosis(report: dict, note: str = "") -> str:
    lines = [f"diagnosis for plugin {report['plugin']}:"]
    if report["declarations"]:
        lines.append("declared:")
        lines += [f"- {d}" for d in report["declarations"]]
    lines.append("findings:")
    lines += [f"- {f}" for f in report["findings"]]
    if note:
        lines.append(note)
    return _clip("\n".join(lines))
