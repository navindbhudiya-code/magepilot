"""Signature injection — resolve REAL framework facts before the coder generates.

The 7B fine-tune hallucinates non-trivial signatures (plugin result types, total
collector collect()). Deterministic rails resolve the truth first — knowledge graph,
then vendor grep, then kb_search — and inject it VERBATIM into the coder prompt so the
model copies instead of guessing. Total collectors need no lookup at all: the canonical
signature is a constant.
"""
import os
import re

from magepilot.magento import archetypes

TOTAL_COLLECTOR_FACTS = """=== FACTS (authoritative — copy these signatures exactly) ===
A custom sales total collector MUST extend \\Magento\\Quote\\Model\\Quote\\Address\\Total\\AbstractTotal
and implement BOTH methods with these exact signatures:

  public function collect(
      \\Magento\\Quote\\Model\\Quote $quote,
      \\Magento\\Quote\\Api\\Data\\ShippingAssignmentInterface $shippingAssignment,
      \\Magento\\Quote\\Model\\Quote\\Address\\Total $total
  ): self

  public function fetch(\\Magento\\Quote\\Model\\Quote $quote, \\Magento\\Quote\\Model\\Quote\\Address\\Total $total): array

collect() MUST call parent::collect($quote, $shippingAssignment, $total) first, then add the
amount via $total->addTotalAmount('<code>', $amount) and $total->addBaseTotalAmount('<code>',
$baseAmount) (or the setTotalAmount/setBaseTotalAmount variants) — never delegate to another
collector and never leave $total unchanged.
Register the collector in etc/sales.xml under section name="quote".
=== END FACTS ==="""

GENERIC_PLUGIN_RULE = """=== FACTS (plugin signature rules) ===
Plugin method naming: before<Method>/after<Method>/around<Method> on the intercepted method.
First parameter is ALWAYS the intercepted class instance ($subject).
For an AFTER plugin, the second parameter ($result) has the SAME TYPE as the intercepted
method's RETURN type — not the type of its arguments.
Always declare the plugin in etc/di.xml: <type name="<intercepted class>">
<plugin name="..." type="<plugin class>"/></type>.
=== END FACTS ==="""


def _graph_return_type(root: str, fqcn: str, method: str) -> str:
    """The method's return type from the project knowledge graph ('' on any miss)."""
    try:
        from magepilot.graph.queries import class_info
        from magepilot.graph.store import GraphStore, graph_path
        path = graph_path(root)
        if not os.path.isfile(path):
            return ""
        store = GraphStore(path)
        try:
            info = class_info(store, fqcn)
        finally:
            store.close()
        if not info:
            return ""
        for sig in info.methods:                      # 'name(params): ReturnType'
            if sig.split("(", 1)[0] == method:
                m = re.search(r"\)\s*:\s*([?\w\\|]+)\s*$", sig)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""


def _vendor_return_type(root: str, fqcn: str, method: str) -> str:
    """Grep vendor/ for the real method declaration and parse its return type / @return."""
    try:
        from magepilot.tools.fs import grep
        out = grep(root, rf"function\s+{re.escape(method)}\s*\(", glob="*.php")
        if not out or out.startswith(("no matches", "grep failed")):
            return ""
        # Magento\Checkout\Model\Cart → match files under .../Checkout/Model/Cart.php or
        # vendor/magento/module-checkout/Model/Cart.php
        parts = fqcn.lstrip("\\").split("\\")
        frag1 = "/".join(parts[1:]) + ".php"
        frag2 = ("module-" + re.sub(r"(?<!^)(?=[A-Z])", "-", parts[1]).lower()
                 + "/" + "/".join(parts[2:]) + ".php") if len(parts) > 2 else ""
        for line in out.splitlines():
            file_part = line.split(":", 1)[0].replace("\\", "/")
            if not (file_part.endswith(frag1) or (frag2 and file_part.endswith(frag2))):
                continue
            m = re.search(r"\)\s*:\s*([?\w\\|]+)", line)
            if m:
                return m.group(1)
            # no inline return type (old core code, e.g. Cart::addProduct) — read the
            # docblock's @return above the declaration
            lm = re.match(r"(.+?):(\d+):", line)
            if lm:
                try:
                    # grep's relative paths can be lexically wrong under symlinked roots
                    # (macOS /var → /private/var); the file is always under <root>/vendor/,
                    # so rebuild the path from that anchor.
                    fp = lm.group(1)
                    idx = fp.find("vendor/")
                    full = os.path.join(root, fp[idx:]) if idx >= 0 \
                        else os.path.join(root, fp)
                    src = open(full, encoding="utf-8",
                               errors="replace").read().splitlines()
                    for ln in reversed(src[max(0, int(lm.group(2)) - 12):int(lm.group(2)) - 1]):
                        rm = re.search(r"@return\s+([?\w\\|$]+)", ln)
                        if rm:
                            t = rm.group(1).split("|")[0]
                            return fqcn if t in ("$this", "self", "static") else t
                        if re.search(r"function\s+\w+\s*\(", ln):
                            break
                except OSError:
                    pass
            return ""
    except Exception:
        pass
    return ""


def _plugin_facts(task: str, root: str) -> str:
    fqcn, method = archetypes.plugin_target(task)
    if not fqcn or not method:
        return GENERIC_PLUGIN_RULE
    ret = _graph_return_type(root, fqcn, method) or _vendor_return_type(root, fqcn, method)
    if ret in ("$this", "self", "static"):
        ret = fqcn
    if not ret:
        return GENERIC_PLUGIN_RULE
    ret = "\\" + ret.lstrip("\\?")
    cap = method[0].upper() + method[1:]
    subj = "\\" + fqcn.lstrip("\\")
    di_name = fqcn.lstrip("\\")
    return f"""=== FACTS (authoritative — copy these signatures exactly) ===
Intercepted method: {subj}::{method}()
Its RETURN type is {ret} — so an AFTER plugin's $result parameter is {ret}, NOT one of
the method's arguments.

  after:  public function after{cap}({subj} $subject, {ret} $result, ...original args...): {ret}
  before: public function before{cap}({subj} $subject, ...original args...): ?array
  around: public function around{cap}({subj} $subject, callable $proceed, ...original args...)

Always return $result from an after plugin (possibly modified). Declare the plugin in
etc/di.xml: <type name="{di_name}"><plugin name="..." type="<plugin class>"/></type>.
=== END FACTS ==="""


def fact_block(task: str, root: str) -> str:
    """The verbatim fact block for this task's archetype — '' when none applies."""
    arch = archetypes.detect(task)
    if arch is None:
        return ""
    if arch.name == "total_collector":
        return TOTAL_COLLECTOR_FACTS
    if arch.name == "plugin":
        return _plugin_facts(task, root)
    return ""
