"""The model-facing plan format and its parser (migrated verbatim from agent/edits.py).

    @@MKDIR app/design/frontend/Vendor/Demo
    @@END

    @@CREATE app/design/frontend/Vendor/Demo/registration.php
    <full file content>
    @@END

    @@EDIT path/to/file.php
    @@FIND
    <exact text to replace>
    @@REPLACE
    <new text>
    @@END

    @@DELETE path/to/file.php
    @@END
"""
import re
import textwrap

_OP = re.compile(r"^@@(CREATE|MKDIR|DELETE|EDIT)[ \t]+(.+?)\s*$")


def _strip_fences(s: str) -> str:
    """Drop ``` / ~~~ markdown fence lines (models sometimes wrap FIND/REPLACE in them)."""
    return "\n".join(ln for ln in s.splitlines() if not re.match(r"^\s*(```|~~~)", ln)).strip("\n")


def _clean(content: str) -> str:
    """Strip markdown fences and any trailing 'Why:' explanation the model may leak into a file."""
    out = []
    for ln in content.splitlines():
        if re.match(r"^\s*(```|~~~)", ln):      # drop fence lines
            continue
        if re.match(r"^\s*Why:\s", ln):         # model's explanation leaked in — stop here
            break
        out.append(ln)
    return "\n".join(out).strip()


def parse_plan(text: str) -> list[dict]:
    """Parse @@-block operations into a list of {op, path, ...} dicts."""
    ops, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        m = _OP.match(lines[i].strip())
        if not m:
            i += 1
            continue
        kind, path = m.group(1).lower(), m.group(2).strip()
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() != "@@END":
            body.append(lines[i])
            i += 1
        i += 1  # consume @@END
        if kind == "create":
            ops.append({"op": "create", "path": path, "content": _clean("\n".join(body))})
        elif kind == "mkdir":
            ops.append({"op": "mkdir", "path": path})
        elif kind == "delete":
            ops.append({"op": "delete", "path": path})
        elif kind == "edit":
            fm = re.search(r"@@FIND\n(.*?)\n@@REPLACE\n(.*)", "\n".join(body), re.S)
            ops.append({"op": "edit", "path": path,
                        "find": _strip_fences(fm.group(1)) if fm else "",
                        "replace": _strip_fences(fm.group(2)) if fm else ""})
    # drop duplicate create/mkdir/delete on the same path (model sometimes repeats)
    seen, deduped = set(), []
    for o in ops:
        key = (o["op"], o["path"])
        if o["op"] in ("create", "mkdir", "delete") and key in seen:
            continue
        seen.add(key)
        deduped.append(o)
    return deduped


def _apply_edit(text: str, find: str, replace: str):
    """Replace `find` with `replace`, tolerant of indentation/whitespace, re-indenting the
    replacement to match where it lands. Returns the new text, or None if `find` can't be located."""
    if not find:
        return None
    if find in text:
        span = (text.index(find), text.index(find) + len(find))
    else:                                   # whitespace-tolerant: match the tokens, any spacing between
        toks = [re.escape(t) for t in find.split()]
        if not toks:
            return None
        m = re.search(r"\s+".join(toks), text)
        if not m:
            return None
        span = (m.start(), m.end())
    line_start = text.rfind("\n", 0, span[0]) + 1
    lead = text[line_start:span[0]]
    if lead.strip() == "":                  # match begins at the line's indentation → absorb + reuse it
        indent, start = lead, line_start
    else:
        indent, start = "", span[0]
    body = textwrap.dedent(replace)
    reindented = "\n".join((indent + ln if ln.strip() else ln) for ln in body.splitlines())
    return text[:start] + reindented + text[span[1]:]
