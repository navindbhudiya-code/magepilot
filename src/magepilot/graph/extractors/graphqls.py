"""GraphQL schema extractor — a small hand-rolled scanner (the .graphqls format is
simple; no grammar dependency needed). Captures type/interface/input declarations and
fields annotated with @resolver(class: "...") → gql_field node + RESOLVES edge."""
import re

_TYPE_RE = re.compile(r"^(type|interface|input|enum)\s+(\w+)", re.M)
# a field declaration, possibly with args (one nesting level for @doc(...) inside them),
# whose directive list — often wrapped onto continuation lines — contains @resolver
_FIELD_RE = re.compile(
    r"^\s*(\w+)\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*:\s*[\[\]\w!\s]*"
    r"(?:@(?!resolver)\w+\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*)*"
    r"@resolver\s*\(\s*class\s*:\s*\"((?:\\\\|[\w\\])+)\"", re.M)


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    src = open(abs_path, encoding="utf-8", errors="replace").read()

    # type blocks in declaration order; fields belong to the preceding type header
    headers = [(m.start(), m.group(1), m.group(2)) for m in _TYPE_RE.finditer(src)]
    for _, kind, name in headers:
        store.add_node("gql_type", name, "gql:" + name, file_id=file_id,
                       module_id=module_id, attrs={"decl": kind})

    def owner_of(pos: int) -> str | None:
        prev = [h for h in headers if h[0] < pos]
        return prev[-1][2] if prev else None

    for m in _FIELD_RE.finditer(src):
        field, resolver = m.group(1), m.group(2).replace("\\\\", "\\").lstrip("\\")
        typ = owner_of(m.start())
        if not typ:
            continue
        qname = f"gql:{typ}.{field}"
        store.add_node("gql_field", f"{typ}.{field}", qname, file_id=file_id,
                       module_id=module_id)
        store.add_edge("RESOLVES", qname, resolver, file_id=file_id,
                       line=src.count("\n", 0, m.start()) + 1)
