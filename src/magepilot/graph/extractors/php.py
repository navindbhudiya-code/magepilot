"""PHP extractor — tree-sitter-php, in-process and error-tolerant: a file with exotic
syntax still yields partial declarations instead of nothing (a parse exception only
quarantines that one file).

Extracted per parse: class/interface/trait declarations (+ extends/implements/use-trait,
resolved against the file's `use` alias map; unresolvable names stay raw in dst_qname);
method declarations with signatures; constructor type-hinted params → INJECTS;
`->dispatch('literal')` on eventManager-ish receivers → DISPATCHES (non-literal →
the pseudo-event `(dynamic)` so coverage gaps are visible, not invisible);
`getTable('x')` / `_init('x')` literals → USES_TABLE. before/after/around method names
become PLUGS_METHOD candidates in the resolve pass.
"""
import re

import tree_sitter
import tree_sitter_php

_LANG = tree_sitter.Language(tree_sitter_php.language_php())
try:
    _PARSER = tree_sitter.Parser(_LANG)
except TypeError:                       # older tree-sitter binding API
    _PARSER = tree_sitter.Parser()
    _PARSER.set_language(_LANG)

_EVENTISH = re.compile(r"event", re.I)
_LITERAL = re.compile(r"^['\"]([\w./-]+)['\"]$")

_DECL_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
}


def _walk(node):
    yield node
    for c in node.named_children:
        yield from _walk(c)


def extract(store, file_id: int, abs_path: str, rel: str, area, module_id) -> None:
    with open(abs_path, "rb") as f:
        src = f.read()
    tree = _PARSER.parse(src)
    root = tree.root_node

    def text(n) -> str:
        return src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    # namespace + use-alias map (file scope; good enough for Magento's 1-class-per-file)
    namespace = ""
    aliases: dict[str, str] = {}
    for n in root.named_children:
        if n.type == "namespace_definition":
            nm = n.child_by_field_name("name")
            if nm is not None:
                namespace = text(nm)
        elif n.type == "namespace_use_declaration":
            for clause in (c for c in _walk(n) if c.type == "namespace_use_clause"):
                qn = next((c for c in clause.named_children if c.type == "qualified_name"
                           or c.type == "name"), None)
                if qn is None:
                    continue
                fq = text(qn).lstrip("\\")
                alias_node = clause.named_children[-1] if clause.named_children[-1] is not qn else None
                alias = text(alias_node) if alias_node is not None else fq.rsplit("\\", 1)[-1]
                aliases[alias] = fq

    def resolve(name: str) -> str:
        name = name.strip()
        if name.startswith("\\"):
            return name.lstrip("\\")
        head, _, rest = name.partition("\\")
        if head in aliases:
            return aliases[head] + ("\\" + rest if rest else "")
        if name in ("self", "static", "parent") or not name[0].isupper():
            return name
        return (namespace + "\\" + name) if namespace else name

    for decl in (n for n in _walk(root) if n.type in _DECL_KINDS):
        kind = _DECL_KINDS[decl.type]
        name_node = decl.child_by_field_name("name")
        if name_node is None:
            continue
        cls_name = text(name_node)
        fqcn = (namespace + "\\" + cls_name) if namespace else cls_name
        attrs = {}
        if any(c.type == "abstract_modifier" for c in decl.children):
            attrs["abstract"] = 1
        store.add_node(kind, cls_name, fqcn, file_id=file_id, module_id=module_id,
                       line_start=decl.start_point[0] + 1, line_end=decl.end_point[0] + 1,
                       attrs=attrs or None)

        for clause in decl.named_children:
            if clause.type == "base_clause":            # extends (classes AND interfaces)
                for t in clause.named_children:
                    if t.type in ("name", "qualified_name"):
                        store.add_edge("EXTENDS", fqcn, resolve(text(t)),
                                       file_id=file_id, line=clause.start_point[0] + 1)
            elif clause.type == "class_interface_clause":   # implements
                for t in clause.named_children:
                    if t.type in ("name", "qualified_name"):
                        store.add_edge("IMPLEMENTS", fqcn, resolve(text(t)),
                                       file_id=file_id, line=clause.start_point[0] + 1)

        body = decl.child_by_field_name("body")
        if body is None:
            continue
        for member in body.named_children:
            if member.type == "use_declaration":            # trait use
                for t in member.named_children:
                    if t.type in ("name", "qualified_name"):
                        store.add_edge("USES_TRAIT", fqcn, resolve(text(t)),
                                       file_id=file_id, line=member.start_point[0] + 1)
            if member.type != "method_declaration":
                continue
            mname_node = member.child_by_field_name("name")
            if mname_node is None:
                continue
            mname = text(mname_node)
            params = member.child_by_field_name("parameters")
            ret = member.child_by_field_name("return_type")
            sig = (text(params) if params is not None else "()") + \
                  ((": " + text(ret)) if ret is not None else "")
            mattrs = {}
            pm = re.match(r"(before|after|around)([A-Z]\w*)", mname)
            if pm:
                mattrs = {"plugin_type": pm.group(1),
                          "target_method": pm.group(2)[0].lower() + pm.group(2)[1:]}
            store.add_node("method", mname, f"{fqcn}::{mname}", file_id=file_id,
                           module_id=module_id, line_start=member.start_point[0] + 1,
                           line_end=member.end_point[0] + 1, signature=sig,
                           attrs=mattrs or None)

            if mname == "__construct" and params is not None:
                for pos, p in enumerate(params.named_children):
                    if p.type not in ("simple_parameter", "property_promotion_parameter"):
                        continue
                    ty = p.child_by_field_name("type")
                    nm = p.child_by_field_name("name")
                    if ty is None:
                        continue
                    tname = text(ty).lstrip("?")
                    if "|" in tname or tname[:1].islower():    # unions/scalars aren't DI
                        continue
                    store.add_edge("INJECTS", fqcn, resolve(tname), file_id=file_id,
                                   line=p.start_point[0] + 1,
                                   attrs={"param": text(nm) if nm is not None else "",
                                          "position": pos})

            # body scan: dispatch literals + table literals
            for call in (c for c in _walk(member) if c.type == "member_call_expression"):
                cname_node = call.child_by_field_name("name")
                if cname_node is None:
                    continue
                cname = text(cname_node)
                args = call.child_by_field_name("arguments")
                first = text(args.named_children[0]) if args is not None and args.named_children else ""
                lit = _LITERAL.match(first.strip())
                line = call.start_point[0] + 1
                if cname == "dispatch":
                    obj = call.child_by_field_name("object")
                    if obj is not None and _EVENTISH.search(text(obj)):
                        if lit:
                            store.ensure_node("event", lit.group(1))
                            store.add_edge("DISPATCHES", f"{fqcn}::{mname}", lit.group(1),
                                           file_id=file_id, line=line)
                        else:
                            store.ensure_node("event", "(dynamic)")
                            store.add_edge("DISPATCHES", f"{fqcn}::{mname}", "(dynamic)",
                                           file_id=file_id, line=line,
                                           attrs={"dynamic": True})
                elif cname in ("getTable", "_init") and lit:
                    store.ensure_node("table", "table:" + lit.group(1))
                    store.add_edge("USES_TABLE", fqcn, "table:" + lit.group(1),
                                   file_id=file_id, line=line)
