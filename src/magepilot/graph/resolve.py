"""Resolve pass: link raw qnames to node ids and materialize PLUGS_METHOD edges from
the di.xml PLUGS_INTO declarations × the before/after/around method-name convention,
walking the target's EXTENDS chain (≤5 levels) so plugins on parents/interfaces still
match. Mismatches are kept with target_has_method=false — that's the typo signal
diagnose_plugin reports."""
import json

MAX_CHAIN = 5


def run(store) -> None:
    db = store.db

    # 1) link edge endpoints to node ids where a node exists for the qname
    for col in ("src", "dst"):
        db.execute(f"""
            UPDATE edges SET {col}_id = (
                SELECT id FROM nodes WHERE nodes.qname = edges.{col}_qname LIMIT 1
            ) WHERE {col}_id IS NULL
              AND EXISTS (SELECT 1 FROM nodes WHERE nodes.qname = edges.{col}_qname)""")

    # 2) PLUGS_METHOD: recompute from scratch (cheap; declarations are few)
    db.execute("DELETE FROM edges WHERE kind='PLUGS_METHOD'")
    plugs = db.execute(
        "SELECT id, src_qname, dst_qname, file_id, area, attrs FROM edges "
        "WHERE kind='PLUGS_INTO'").fetchall()
    for edge in plugs:
        plugin_fqcn, target = edge["src_qname"], edge["dst_qname"]
        if plugin_fqcn.startswith("(plugin:"):
            continue                       # a disable-only declaration — nothing to map
        decl_attrs = json.loads(edge["attrs"] or "{}")
        target_methods = _method_names(db, target)
        for m in db.execute(
                "SELECT qname, attrs FROM nodes WHERE kind='method' AND qname LIKE ? "
                "AND attrs IS NOT NULL", (plugin_fqcn + "::%",)):
            mattrs = json.loads(m["attrs"] or "{}")
            tm = mattrs.get("target_method")
            if not tm:
                continue
            store.add_edge(
                "PLUGS_METHOD", m["qname"], f"{target}::{tm}",
                file_id=edge["file_id"], area=edge["area"],
                attrs={"type": mattrs.get("plugin_type"),
                       "plugin_name": decl_attrs.get("plugin_name", ""),
                       "sort_order": decl_attrs.get("sort_order", 0),
                       "disabled": decl_attrs.get("disabled", False),
                       "target_has_method": tm in target_methods})
    # 3) COVERS: Test\Unit mirror-path convention → covered class (recomputed)
    db.execute("DELETE FROM edges WHERE kind='COVERS'")
    for row in db.execute(
            "SELECT qname, file_id FROM nodes WHERE kind='class' "
            "AND qname LIKE '%Test\\Unit\\%' AND qname LIKE '%Test'").fetchall():
        covered = row["qname"].replace("\\Test\\Unit\\", "\\")[:-len("Test")]
        if db.execute("SELECT 1 FROM nodes WHERE qname=? AND kind IN "
                      "('class','interface','trait')", (covered,)).fetchone():
            store.add_edge("COVERS", row["qname"], covered, file_id=row["file_id"])
    db.commit()


def module_load_order(db) -> dict[str, int]:
    """Kahn topological order over DEPENDS_ON_MODULE (a module loads AFTER its
    sequence dependencies). Higher index = later load = its di.xml wins ties."""
    deps: dict[str, set] = {}
    names = {r["name"] for r in db.execute("SELECT name FROM modules")}
    for n in names:
        deps[n] = set()
    for r in db.execute("SELECT src_qname, dst_qname FROM edges "
                        "WHERE kind='DEPENDS_ON_MODULE'"):
        src = r["src_qname"].removeprefix("module:")
        dst = r["dst_qname"].removeprefix("module:")
        if src in deps:
            deps[src].add(dst)
    order, placed, idx = {}, set(), 0
    while deps:
        ready = sorted(n for n, d in deps.items() if not (d & set(deps)) - placed)
        if not ready:                       # a cycle — place the rest deterministically
            ready = sorted(deps)
        for n in ready:
            order[n] = idx
            idx += 1
            placed.add(n)
            deps.pop(n)
    return order


def chain_of(db, fqcn: str, max_depth: int = MAX_CHAIN) -> list[str]:
    """fqcn + its EXTENDS ancestors + interfaces implemented along the chain."""
    chain, frontier = [fqcn], [fqcn]
    for _ in range(max_depth):
        nxt = []
        for q in frontier:
            for row in db.execute(
                    "SELECT dst_qname FROM edges WHERE kind IN ('EXTENDS','IMPLEMENTS') "
                    "AND src_qname=?", (q,)):
                if row["dst_qname"] not in chain:
                    chain.append(row["dst_qname"])
                    nxt.append(row["dst_qname"])
        if not nxt:
            break
        frontier = nxt
    return chain


def _method_names(db, fqcn: str) -> set[str]:
    """Method names of fqcn including inherited (EXTENDS/IMPLEMENTS chain)."""
    names = set()
    for q in chain_of(db, fqcn):
        for row in db.execute(
                "SELECT name FROM nodes WHERE kind='method' AND qname LIKE ?", (q + "::%",)):
            names.add(row["name"])
    return names
