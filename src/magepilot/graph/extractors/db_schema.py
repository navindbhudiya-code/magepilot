"""db_schema.xml extractor: table nodes (columns/engine/comment in attrs, capped) +
OWNS_TABLE (module → table) + REFERENCES_TABLE (foreign-key constraints)."""
import defusedxml.ElementTree as ET

_XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
MAX_COLUMNS = 40


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    root = ET.parse(abs_path).getroot()
    owner = store.module_qname(module_id)
    for table in root.findall("table"):
        name = (table.get("name") or "").strip()
        if not name:
            continue
        cols = [c.get("name") for c in table.findall("column") if c.get("name")]
        qname = "table:" + name
        # Many modules extend the SAME table (GiftMessage adds a column to sales_order),
        # so per-declaration data lives on the OWNS_TABLE edge — per-file, incremental-safe —
        # and table_info() aggregates: union of columns, owner = the largest declarer.
        store.ensure_node("table", qname)
        if owner:
            store.add_edge("OWNS_TABLE", owner, qname, file_id=file_id,
                           attrs={"engine": table.get("engine"),
                                  "comment": table.get("comment"),
                                  "columns": cols[:MAX_COLUMNS],
                                  "n_columns": len(cols)})
        for con in table.findall("constraint"):
            if (con.get(_XSI) or "") == "foreign" and con.get("referenceTable"):
                store.ensure_node("table", "table:" + con.get("referenceTable"))
                store.add_edge("REFERENCES_TABLE", qname,
                               "table:" + con.get("referenceTable"), file_id=file_id,
                               attrs={"column": con.get("column"),
                                      "reference_column": con.get("referenceColumn"),
                                      "on_delete": con.get("onDelete")})
