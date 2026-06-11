"""Graph query API v1 — the seven queries (docs/architecture/05). Every list is ranked
(app/code before vendor, enabled before disabled) and capped: results must fit a 7B
model's context as compact observations."""
import json
from dataclasses import dataclass, field

from magepilot.graph.resolve import chain_of

CAP = 12


@dataclass(frozen=True)
class Ref:
    qname: str
    kind: str
    file: str = ""
    line: int = 0
    module: str = ""
    vendor: bool = False


@dataclass
class ClassInfo:
    ref: Ref
    extends: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    injects: list[tuple[str, str]] = field(default_factory=list)   # (param, type)
    methods: list[str] = field(default_factory=list)               # 'name(sig)'
    preferred_to: list[str] = field(default_factory=list)          # PREFERS away
    counts: dict = field(default_factory=dict)                     # wiring counts


@dataclass(frozen=True)
class PluginInfo:
    plugin_fqcn: str
    plugin_name: str
    sort_order: int
    disabled: bool
    area: str
    declared_in: str
    target: str
    methods: tuple = ()        # (type, target_method, target_has_method)


@dataclass(frozen=True)
class ObserverInfo:
    observer_fqcn: str
    observer_name: str
    area: str
    disabled: bool
    declared_in: str


def _loc(db, file_id, line=None) -> str:
    if not file_id:
        return ""
    row = db.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        return ""
    return row["path"] + (f":{line}" if line else "")


def _node_ref(db, row) -> Ref:
    f = db.execute("SELECT path, in_vendor FROM files WHERE id=?",
                   (row["file_id"],)).fetchone() if row["file_id"] else None
    m = db.execute("SELECT name FROM modules WHERE id=?",
                   (row["module_id"],)).fetchone() if row["module_id"] else None
    return Ref(qname=row["qname"], kind=row["kind"],
               file=f["path"] if f else "", line=row["line_start"] or 0,
               module=m["name"] if m else "", vendor=bool(f and f["in_vendor"]))


def partial_note(store) -> str:
    """Non-empty when answering from an incomplete graph."""
    state = store.get_meta("build_state")
    if state and state != "complete":
        return f"note: graph build is incomplete (state: {state}) — answers may be partial"
    return ""


# ------------------------------------------------------------------ 1. find_symbol
def find_symbol(store, q: str, kind: str = None, limit: int = 10) -> list[Ref]:
    db = store.db
    q = (q or "").strip().lstrip("\\")
    seen, out = set(), []

    def take(rows):
        for r in rows:
            if r["qname"] not in seen and (kind is None or r["kind"] == kind):
                seen.add(r["qname"])
                out.append(_node_ref(db, r))

    take(db.execute("SELECT * FROM nodes WHERE qname=?", (q,)))
    take(db.execute("SELECT n.* FROM nodes n LEFT JOIN files f ON f.id=n.file_id "
                    "WHERE n.name=? COLLATE NOCASE "
                    "ORDER BY COALESCE(f.in_vendor,1), n.kind", (q,)))
    if len(out) < limit:
        fts = " ".join(t + "*" for t in q.replace("\\", " ").replace("_", " ").split()[:4])
        try:
            take(db.execute(
                "SELECT n.* FROM node_fts ft JOIN nodes n ON n.id=ft.rowid "
                "LEFT JOIN files f ON f.id=n.file_id WHERE node_fts MATCH ? "
                "ORDER BY COALESCE(f.in_vendor,1), bm25(node_fts) LIMIT ?",
                (fts, limit * 2)))
        except Exception:
            pass
    return out[:limit]


# ------------------------------------------------------------------ 2. class_info
def class_info(store, fqcn: str) -> ClassInfo | None:
    db = store.db
    fqcn = fqcn.strip().lstrip("\\")
    row = db.execute("SELECT * FROM nodes WHERE qname=? AND kind IN "
                     "('class','interface','trait','virtual_type')", (fqcn,)).fetchone()
    if row is None:
        return None
    info = ClassInfo(ref=_node_ref(db, row))
    info.extends = [r["dst_qname"] for r in db.execute(
        "SELECT dst_qname FROM edges WHERE kind='EXTENDS' AND src_qname=?", (fqcn,))]
    info.implements = [r["dst_qname"] for r in db.execute(
        "SELECT dst_qname FROM edges WHERE kind='IMPLEMENTS' AND src_qname=?", (fqcn,))]
    info.traits = [r["dst_qname"] for r in db.execute(
        "SELECT dst_qname FROM edges WHERE kind='USES_TRAIT' AND src_qname=?", (fqcn,))]
    info.injects = [(json.loads(r["attrs"] or "{}").get("param", ""), r["dst_qname"])
                    for r in db.execute(
        "SELECT dst_qname, attrs FROM edges WHERE kind='INJECTS' AND src_qname=? "
        "ORDER BY id", (fqcn,))]
    info.methods = [r["name"] + (r["signature"] or "()") for r in db.execute(
        "SELECT name, signature FROM nodes WHERE kind='method' AND qname LIKE ? "
        "ORDER BY line_start LIMIT 15", (fqcn + "::%",))]
    info.preferred_to = [r["dst_qname"] for r in db.execute(
        "SELECT dst_qname FROM edges WHERE kind='PREFERS' AND src_qname=?", (fqcn,))]
    info.counts = {
        "plugins": len(plugins_for(store, fqcn)),
        "implementors": db.execute("SELECT COUNT(*) FROM edges WHERE kind='IMPLEMENTS' "
                                   "AND dst_qname=?", (fqcn,)).fetchone()[0],
        "injectors": db.execute("SELECT COUNT(*) FROM edges WHERE kind='INJECTS' "
                                "AND dst_qname=?", (fqcn,)).fetchone()[0],
    }
    return info


# ------------------------------------------------------------------ 3. plugins_for
def plugins_for(store, fqcn: str, method: str = None, area: str = None) -> list[PluginInfo]:
    """Plugins intercepting fqcn or anything in its EXTENDS/IMPLEMENTS chain.
    Area-specific declarations override global ones for the same plugin_name."""
    db = store.db
    fqcn = fqcn.strip().lstrip("\\")
    chain = chain_of(db, fqcn)
    rows = []
    for target in chain:
        rows += db.execute(
            "SELECT * FROM edges WHERE kind='PLUGS_INTO' AND dst_qname=?", (target,)).fetchall()

    merged: dict[str, dict] = {}
    for r in rows:
        attrs = json.loads(r["attrs"] or "{}")
        name = attrs.get("plugin_name") or r["src_qname"]
        cur = merged.get(name)
        fqcn = r["src_qname"]
        if cur is not None and fqcn.startswith("(plugin:"):
            fqcn = cur["fqcn"]            # a disable-only override keeps the declared class
        # requested area (or any non-global) overrides global for the same plugin name
        if cur is None or (cur["area"] == "global" and r["area"] != "global"
                           and (area is None or r["area"] == area)):
            merged[name] = {"row": r, "attrs": attrs, "area": r["area"], "fqcn": fqcn}

    out = []
    for name, m in merged.items():
        if area and m["area"] not in ("global", area):
            continue
        r, attrs = m["row"], m["attrs"]
        methods = tuple(
            (json.loads(e["attrs"] or "{}").get("type"),
             e["dst_qname"].split("::")[-1],
             json.loads(e["attrs"] or "{}").get("target_has_method", True))
            for e in db.execute(
                "SELECT dst_qname, attrs FROM edges WHERE kind='PLUGS_METHOD' "
                "AND src_qname LIKE ?", (m["fqcn"] + "::%",))
            if method is None or e["dst_qname"].endswith("::" + method))
        if method is not None and not methods and not m["fqcn"].startswith("(plugin:"):
            continue
        out.append(PluginInfo(
            plugin_fqcn=m["fqcn"], plugin_name=name,
            sort_order=attrs.get("sort_order", 0), disabled=attrs.get("disabled", False),
            area=m["area"], declared_in=_loc(db, r["file_id"], r["line"]),
            target=r["dst_qname"], methods=methods))
    out.sort(key=lambda p: (p.disabled, p.sort_order, p.plugin_name))
    return out[:CAP]


# ------------------------------------------------------------------ 4. preference_for
def preference_for(store, fqcn: str, area: str = None) -> list[dict]:
    db = store.db
    fqcn = fqcn.strip().lstrip("\\")
    rows = db.execute("SELECT e.*, f.in_vendor iv FROM edges e "
                      "LEFT JOIN files f ON f.id=e.file_id "
                      "WHERE e.kind='PREFERS' AND e.src_qname=?", (fqcn,)).fetchall()
    prefs = [{"impl": r["dst_qname"], "area": r["area"],
              "declared_in": _loc(db, r["file_id"], r["line"]),
              "vendor": bool(r["iv"])} for r in rows
             if area is None or r["area"] in ("global", area)]
    # winner approximation: app/code beats vendor; later declaration beats earlier
    for p in prefs:
        p["winner"] = False
    if prefs:
        best = sorted(prefs, key=lambda p: (p["vendor"],))[0]
        best["winner"] = True
    return prefs


# ------------------------------------------------------------------ 5. observers_of
def observers_of(store, event: str, area: str = None) -> tuple[list[ObserverInfo], list[str]]:
    """(observers, dispatch_sites) for an event name."""
    db = store.db
    obs = []
    for r in db.execute("SELECT * FROM edges WHERE kind='OBSERVES' AND dst_qname=?", (event,)):
        attrs = json.loads(r["attrs"] or "{}")
        if area and r["area"] not in ("global", area):
            continue
        obs.append(ObserverInfo(observer_fqcn=r["src_qname"],
                                observer_name=attrs.get("observer_name", ""),
                                area=r["area"], disabled=attrs.get("disabled", False),
                                declared_in=_loc(db, r["file_id"], r["line"])))
    obs.sort(key=lambda o: (o.disabled, o.area, o.observer_name))
    dispatchers = [f"{r['src_qname']} ({_loc(db, r['file_id'], r['line'])})"
                   for r in db.execute(
            "SELECT * FROM edges WHERE kind='DISPATCHES' AND dst_qname=? LIMIT ?",
            (event, CAP))]
    return obs[:CAP], dispatchers


# ------------------------------------------------------------------ 6. impact_of
def impact_of(store, fqcn: str) -> dict:
    """Exact counts + ≤3 examples per relation — 'what breaks if I change this?'."""
    db = store.db
    fqcn = fqcn.strip().lstrip("\\")
    out = {"target": fqcn, "relations": {}}
    rels = {
        "implementors": ("IMPLEMENTS", "dst"),
        "subclasses": ("EXTENDS", "dst"),
        "injectors": ("INJECTS", "dst"),
        "plugins": ("PLUGS_INTO", "dst"),
        "preferences": ("PREFERS", "src"),
        "di_arguments": ("DI_ARGUMENT", "dst"),
        "dispatches_from": ("DISPATCHES", "src"),
    }
    for label, (kind, col) in rels.items():
        like = fqcn + "::%" if label == "dispatches_from" else None
        where = f"{col}_qname LIKE ?" if like else f"{col}_qname = ?"
        n = db.execute(f"SELECT COUNT(*) FROM edges WHERE kind=? AND {where}",
                       (kind, like or fqcn)).fetchone()[0]
        if not n:
            continue
        other = "dst" if col == "src" else "src"
        rows = db.execute(
            f"SELECT e.{other}_qname x, e.file_id, e.line FROM edges e "
            f"LEFT JOIN files f ON f.id=e.file_id WHERE e.kind=? AND e.{where} "
            f"ORDER BY COALESCE(f.in_vendor,1) LIMIT 3", (kind, like or fqcn)).fetchall()
        out["relations"][label] = {
            "count": n,
            "examples": [f"{r['x']} ({_loc(db, r['file_id'], r['line'])})" for r in rows]}
    return out


# ------------------------------------------------------------------ 7. diagnose_plugin
def diagnose_plugin(store, plugin_fqcn: str) -> dict:
    """Ordered checks for 'why is my plugin not firing'."""
    db = store.db
    plugin_fqcn = plugin_fqcn.strip().lstrip("\\")
    report = {"plugin": plugin_fqcn, "declarations": [], "findings": []}

    decls = db.execute("SELECT * FROM edges WHERE kind='PLUGS_INTO' AND src_qname=?",
                       (plugin_fqcn,)).fetchall()
    if not decls:
        report["findings"].append(
            "NOT DECLARED: no <plugin> entry for this class in any indexed di.xml — "
            "declare it under <type name=\"...\"><plugin name=\"...\" type=\"...\"/>")
        return report

    plugin_methods = db.execute(
        "SELECT name, attrs FROM nodes WHERE kind='method' AND qname LIKE ? "
        "AND attrs IS NOT NULL", (plugin_fqcn + "::%",)).fetchall()

    for d in decls:
        attrs = json.loads(d["attrs"] or "{}")
        target, pname = d["dst_qname"], attrs.get("plugin_name", "")
        report["declarations"].append(
            f"{pname or '(unnamed)'} -> {target} [{d['area']}] sortOrder="
            f"{attrs.get('sort_order', 0)}"
            + (" DISABLED" if attrs.get("disabled") else "")
            + f"  ({_loc(db, d['file_id'], d['line'])})")
        if attrs.get("disabled"):
            report["findings"].append(
                f"DISABLED where declared ({_loc(db, d['file_id'], d['line'])})")
        # disabled by ANOTHER module's di.xml (same plugin_name, no type=) — grep never finds this
        if pname:
            for o in db.execute("SELECT * FROM edges WHERE kind='PLUGS_INTO' "
                                "AND dst_qname=? AND id != ?", (target, d["id"])):
                oattrs = json.loads(o["attrs"] or "{}")
                if oattrs.get("plugin_name") == pname and oattrs.get("disabled"):
                    report["findings"].append(
                        f"DISABLED ELSEWHERE by {_loc(db, o['file_id'], o['line'])} "
                        f"[{o['area']}]")
        if d["area"] not in ("global", "frontend"):
            report["findings"].append(
                f"AREA: declared only in [{d['area']}] — it will not fire in other areas")
        # method-name mismatches (typo detection)
        target_methods = {r["name"].lower() for q in chain_of(db, target)
                          for r in db.execute("SELECT name FROM nodes WHERE kind='method' "
                                              "AND qname LIKE ?", (q + "::%",))}
        for m in plugin_methods:
            ma = json.loads(m["attrs"] or "{}")
            tm = ma.get("target_method")
            if tm and target_methods and tm.lower() not in target_methods:
                report["findings"].append(
                    f"METHOD MISMATCH: {m['name']}() expects {target}::{tm}() which "
                    f"does not exist on the target (typo?)")
        # target replaced by a preference → the declared class is never instantiated
        for p in db.execute("SELECT dst_qname, file_id, line FROM edges "
                            "WHERE kind='PREFERS' AND src_qname=?", (target,)):
            report["findings"].append(
                f"PREFERENCE SHADOW: {target} is preferred to {p['dst_qname']} "
                f"({_loc(db, p['file_id'], p['line'])}) — if the plugin targets the "
                f"concrete class instead of the interface it may never intercept")
        # module disabled
        mrow = db.execute(
            "SELECT m.name, m.enabled FROM nodes n JOIN modules m ON m.id=n.module_id "
            "WHERE n.qname=? AND n.kind IN ('class','interface')", (plugin_fqcn,)).fetchone()
        if mrow and mrow["enabled"] == 0:
            report["findings"].append(f"MODULE DISABLED: {mrow['name']} is disabled in "
                                      f"app/etc/config.php")
    if not report["findings"]:
        report["findings"].append("no structural problem found — check that the target "
                                  "method is public and non-final, and clear the config cache")
    return report
