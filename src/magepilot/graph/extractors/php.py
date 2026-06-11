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
        in_vendor = rel.startswith("vendor/")
        methods = []
        prop_types: dict[str, str] = {}      # '$repo' → FQCN (from promoted ctor params)
        parent_fqcn = next((resolve(text(t)) for clause in decl.named_children
                            if clause.type == "base_clause"
                            for t in clause.named_children
                            if t.type in ("name", "qualified_name")), None)

        # pass 1: collect members + the constructor's param→type map (the property map
        # that makes $this->prop->method() calls resolvable in pass 2)
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
            methods.append((member, mname))
            params = member.child_by_field_name("parameters")
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
                    resolved = resolve(tname)
                    store.add_edge("INJECTS", fqcn, resolved, file_id=file_id,
                                   line=p.start_point[0] + 1,
                                   attrs={"param": text(nm) if nm is not None else "",
                                          "position": pos})
                    if p.type == "property_promotion_parameter" and nm is not None:
                        prop_types[text(nm).lstrip("$")] = resolved

        # pass 2: method nodes + body scans
        for member, mname in methods:
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
            caller = f"{fqcn}::{mname}"

            for call in (c for c in _walk(member) if c.type == "member_call_expression"):
                cname_node = call.child_by_field_name("name")
                if cname_node is None:
                    continue
                cname = text(cname_node)
                args = call.child_by_field_name("arguments")
                first = text(args.named_children[0]) if args is not None and args.named_children else ""
                lit = _LITERAL.match(first.strip())
                line = call.start_point[0] + 1
                obj = call.child_by_field_name("object")
                obj_text = text(obj) if obj is not None else ""
                if cname == "dispatch":
                    if obj is not None and _EVENTISH.search(obj_text):
                        if lit:
                            store.ensure_node("event", lit.group(1))
                            store.add_edge("DISPATCHES", caller, lit.group(1),
                                           file_id=file_id, line=line)
                        else:
                            store.ensure_node("event", "(dynamic)")
                            store.add_edge("DISPATCHES", caller, "(dynamic)",
                                           file_id=file_id, line=line,
                                           attrs={"dynamic": True})
                        continue
                elif cname in ("getTable", "_init") and lit:
                    store.ensure_node("table", "table:" + lit.group(1))
                    store.add_edge("USES_TABLE", fqcn, "table:" + lit.group(1),
                                   file_id=file_id, line=line)
                    continue
                # best-effort CALLS — app/code only (vendor would add millions of edges)
                if in_vendor or not cname[:1].isalpha():
                    continue
                target = None
                if obj_text == "$this":
                    target = f"{fqcn}::{cname}"
                else:
                    pm2 = re.match(r"\$this->(\w+)$", obj_text)
                    if pm2 and pm2.group(1) in prop_types:
                        target = f"{prop_types[pm2.group(1)]}::{cname}"
                if target:
                    store.add_edge("CALLS", caller, target, file_id=file_id, line=line,
                                   attrs={"confidence": "static"})

            if in_vendor:
                continue
            for call in (c for c in _walk(member) if c.type == "scoped_call_expression"):
                scope = call.child_by_field_name("scope")
                cname_node = call.child_by_field_name("name")
                if scope is None or cname_node is None:
                    continue
                stext, cname = text(scope), text(cname_node)
                if stext in ("self", "static"):
                    target = f"{fqcn}::{cname}"
                elif stext == "parent":
                    if not parent_fqcn:
                        continue
                    target = f"{parent_fqcn}::{cname}"
                elif stext[:1].isupper() or stext.startswith("\\"):
                    target = f"{resolve(stext)}::{cname}"
                else:
                    continue
                store.add_edge("CALLS", f"{fqcn}::{mname}", target, file_id=file_id,
                               line=call.start_point[0] + 1,
                               attrs={"confidence": "static"})
