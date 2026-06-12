#!/usr/bin/env python3
"""Deterministic make-task eval — replays the MODEL_IMPROVEMENT_REPORT failures.

Sends each task to the live coder role (the served fine-tune on :8080) with the exact
scaffolding system prompt `magepilot make` uses, then scores the RAW plan with the
same deterministic checkers the agent rails use: file-set completeness (archetype
manifests), php -l, XML validity + schemaLocation, signature regexes, cross-file
wiring (MP016/MP017). No model judges anything.

NOTE: this measures the BARE model (one shot, no manifest re-prompts, no repair loop,
no fact injection) so scorecards are comparable across fine-tune versions. The
production `make` flow layers the Track-A rails on top of whatever the model emits.

    PYTHONPATH=src mlx-env/bin/python eval/run_eval.py --label v2-baseline

Outputs: eval/reports/make-eval-<label>.md (+ .json sidecar for scriptable diffs),
raw plans under eval/reports/raw/<label>/.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

from magepilot import config  # noqa: E402
from magepilot.edits import validate  # noqa: E402
from magepilot.edits.blocks import parse_plan  # noqa: E402
from magepilot.edits.scaffold import SYSTEM  # noqa: E402
from magepilot.llm.router import get_router  # noqa: E402
from magepilot.magento import archetypes  # noqa: E402
from magepilot.safety import scan as safety_scan  # noqa: E402
from magepilot.safety.lint_magento import lint_content  # noqa: E402

# The exact failing tasks from MODEL_IMPROVEMENT_REPORT.md (anonymized naming), plus
# the module-creation control that mostly passed. Each `sigs` entry is
# (name, file-path regex to select the op, content regex, must_match).
TASKS = [
    {
        "id": "total-collector",
        "archetype": "total_collector",
        "task": "Add a fixed 5.00 handling fee to every order using a custom total "
                "collector extending AbstractTotal plus etc/sales.xml. Module Vendor_CartFee.",
        "sigs": [
            ("extends AbstractTotal", r"\.php$", r"extends\s+\\?(?:[\w\\]+\\)?AbstractTotal\b", True),
            ("collect(Quote, ShippingAssignmentInterface, Total)", r"\.php$",
             r"function\s+collect\s*\([^)]*Quote\b[^)]*ShippingAssignment[^)]*Total\b", True),
            ("fetch() implemented", r"\.php$", r"function\s+fetch\s*\(", True),
            ("mutates $total", r"\.php$",
             r"\$total->(?:add|set)(?:Base)?TotalAmount\s*\(", True),
            ("sales.xml registers the collector", r"sales\.xml$", r"<item\s+name=", True),
        ],
    },
    {
        "id": "cart-after-plugin",
        "archetype": "plugin",
        "task": "Create etc/di.xml AND the plugin class for an after plugin on "
                "Magento\\Checkout\\Model\\Cart::addProduct that logs the added product. "
                "Module Vendor_CartLogger.",
        "sigs": [
            ("afterAddProduct present", r"\.php$", r"function\s+afterAddProduct\s*\(", True),
            ("$result typed as Cart (the RETURN type)", r"\.php$",
             r"function\s+afterAddProduct\s*\(\s*(?:\\?Magento\\Checkout\\Model\\)?Cart\s+\$\w+\s*,"
             r"\s*(?:\\?Magento\\Checkout\\Model\\)?Cart\s+\$\w+", True),
            ("$result NOT typed as Product", r"\.php$",
             r"function\s+afterAddProduct\s*\([^,]+,\s*(?:\\?[\w\\]*\\)?Product(?:Interface)?\s+\$",
             False),
            ("di.xml declares the plugin", r"di\.xml$", r"<plugin\s+name=", True),
        ],
    },
    {
        "id": "hyva-child-theme",
        "archetype": "theme",
        "task": "Create a Hyva child theme Vendor/custom: theme.xml with parent Hyva/default, "
                "registration.php and composer.json.",
        "sigs": [
            ("parent is Hyva/default", r"theme\.xml$", r"<parent>\s*Hyva/default\s*</parent>", True),
            ("registers a THEME component", r"registration\.php$",
             r"ComponentRegistrar::THEME", True),
        ],
    },
    {
        "id": "cart-observer",
        "archetype": "observer",
        "task": "Create an observer on checkout_cart_add_product_complete that logs the added "
                "product, with the events.xml wiring. Module Vendor_FreeGift.",
        "sigs": [
            ("implements ObserverInterface", r"\.php$",
             r"implements\s+[^{]*\bObserverInterface\b", True),
            ("events.xml subscribes the event", r"events\.xml$",
             r'<event\s+name="checkout_cart_add_product_complete"', True),
        ],
    },
    {
        "id": "cli-count-enabled",
        "archetype": "cli_command",
        "task": "Create a CLI command vendor:product:count-enabled that counts enabled products. "
                "Module Vendor_Tools.",
        "sigs": [
            ("extends Symfony Command", r"\.php$", r"extends\s+\\?(?:[\w\\]+\\)?Command\b", True),
            ("di.xml registers in CommandListInterface", r"di\.xml$",
             r"Console\\+CommandListInterface", True),
            ("status filtered as EAV (addAttributeToFilter)", r"\.php$",
             r"addAttributeToFilter\s*\(\s*['\"]status['\"]", True),
            ("no raw status column on catalog_product_entity", r"\.php$",
             r"catalog_product_entity\b(?!_)[\s\S]{0,200}status", False),
        ],
    },
    {
        "id": "module-control",
        "archetype": "module",
        "task": "Create a module called Vendor_CartFee with registration.php, etc/module.xml "
                "and composer.json.",
        "sigs": [
            ("module.xml has <config> wrapper", r"module\.xml$", r"<config\b", True),
            ("module.xml schemaLocation is module.xsd", r"module\.xml$",
             r"urn:magento:framework:Module/etc/module\.xsd", True),
            ("no deprecated setup_version", r"module\.xml$", r"setup_version", False),
        ],
    },
]


def served_model() -> str:
    try:
        with urllib.request.urlopen(config.MODEL_SERVER.rstrip("/") + "/models", timeout=10) as r:
            data = json.loads(r.read())
        ids = [m.get("id", "") for m in data.get("data", [])]
        return ", ".join(i for i in ids if i) or "?"
    except Exception as e:
        return f"unreachable ({e})"


def generate(task: str) -> str:
    """One bare coder-role shot — exactly what generate_plan sends, minus the rails."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    text = get_router().complete("coder", messages, stop=["<|im_end|>"],
                                 sampling={"temperature": 0.0, "max_tokens": 2400},
                                 timeout=600)
    return text.split("<|im_end|>")[0]


def score(raw: str, spec: dict) -> dict:
    """All deterministic criteria for one task. Returns {criterion: bool|'skipped'}."""
    ops = parse_plan(raw)
    creates = [o for o in ops if o["op"] == "create"]
    crit = {}

    # ---- parse + format discipline ----
    crit["parse: ≥1 op"] = bool(ops)
    tail = raw.rsplit("@@END", 1)[-1] if "@@END" in raw else raw
    crit["format: no fences"] = "```" not in raw
    crit["format: no Why: prose"] = not re.search(r"^\s*Why:", raw, re.M)
    crit["format: nothing after final @@END"] = "@@END" in raw and not tail.strip()

    # ---- file-set completeness via the archetype manifest ----
    arch = next(a for a in archetypes.ARCHETYPES if a.name == spec["archetype"])
    gaps = archetypes.manifest_gaps(arch, ops)
    crit["files: manifest complete"] = not gaps
    for g in gaps:
        crit[f"files: missing {g.kind}"] = False

    # ---- php -l / XML / json ----
    php_results, xml_ok = [], []
    for op in creates:
        p = op["path"].replace("\\", "/")
        if p.endswith((".php", ".phtml")):
            php_results.append(validate.php_lint_error(op.get("content", "")) is None)
        elif p.endswith(".xml"):
            xml_ok.append(validate.xml_error(p, op.get("content", "")) is None)
        elif p.endswith("composer.json"):
            try:
                json.loads(op.get("content", ""))
                xml_ok.append(True)
            except Exception:
                xml_ok.append(False)
    import shutil
    if not shutil.which("php"):
        crit["php -l"] = "skipped"
    else:
        crit["php -l"] = bool(php_results) and all(php_results)
    crit["xml/json valid + schemaLocation"] = bool(xml_ok) and all(xml_ok)

    # ---- signatures ----
    for name, path_re, content_re, must in spec["sigs"]:
        rx_p, rx_c = re.compile(path_re), re.compile(content_re)
        hit = any(rx_p.search(o["path"].replace("\\", "/")) and rx_c.search(o.get("content", ""))
                  for o in creates)
        crit[f"sig: {name}"] = hit if must else not hit

    # ---- cross-file wiring + content lint (the BLOCK rules) ----
    plan_findings = safety_scan.scan_plan(ops)
    crit["wiring: no MP016/MP017"] = not plan_findings
    lint_block = [f for o in creates
                  for f in lint_content(o["path"], o.get("content", ""))
                  if f.severity == "block"]
    crit["lint: no BLOCK findings"] = not lint_block
    return crit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="run label, e.g. v2-baseline / v5")
    ap.add_argument("--only", default="", help="comma-separated task ids to run")
    args = ap.parse_args()

    model = served_model()
    if model.startswith("unreachable"):
        print(f"model server {config.MODEL_SERVER} is {model} — run `./magepilot serve` first")
        return 1
    print(f"eval '{args.label}' against: {model}\n")
    if "magento" not in model.lower():
        # mlx_lm.server maps --adapter-path ONLY onto the request-model name
        # "default_model"; named-model requests (which the router sends) silently get
        # the BASE weights. Adapter-served evals therefore score the base model —
        # fuse the adapter and serve the fused path instead.
        print("⚠ WARNING: only the base model id is served — if you meant to eval an "
              "adapter via --adapter-path, this run is scoring the BASE model. "
              "Fuse first (mlx_lm.fuse) and serve the fused path.\n")

    raw_dir = os.path.join(HERE, "reports", "raw", args.label)
    os.makedirs(raw_dir, exist_ok=True)
    only = {t for t in args.only.split(",") if t}

    results, n_pass = {}, 0
    tasks = [t for t in TASKS if not only or t["id"] in only]
    for spec in tasks:
        print(f"→ {spec['id']} …", end=" ", flush=True)
        raw = generate(spec["task"])
        open(os.path.join(raw_dir, spec["id"] + ".txt"), "w").write(raw)
        crit = score(raw, spec)
        hard = [v for v in crit.values() if v != "skipped"]
        ok = all(hard)
        n_pass += ok
        results[spec["id"]] = {"pass": ok, "criteria": crit}
        n_ok = sum(1 for v in hard if v)
        print(("PASS" if ok else "FAIL") + f"  ({n_ok}/{len(hard)} criteria)")

    # ---- scorecard ----
    from datetime import date
    md = [f"# make-eval — {args.label}", "",
          f"**Model:** {model}  ·  **Date:** {date.today().isoformat()}  ·  "
          f"**Decoding:** temperature 0, max_tokens 2400, stop `<|im_end|>`",
          "",
          "Bare-model one-shot (no manifest re-prompt / repair / fact injection) — "
          "scores the fine-tune itself; the production `make` rails sit on top.",
          "",
          "| task | criteria passed | verdict |", "|---|---|---|"]
    for tid, r in results.items():
        hard = [v for v in r["criteria"].values() if v != "skipped"]
        md.append(f"| {tid} | {sum(1 for v in hard if v)}/{len(hard)} | "
                  f"{'✅ PASS' if r['pass'] else '❌ FAIL'} |")
    md += ["", f"**Total: {n_pass}/{len(tasks)} tasks pass**", "", "## Failures", ""]
    for tid, r in results.items():
        fails = [k for k, v in r["criteria"].items() if v is False]
        if fails:
            md.append(f"### {tid}")
            md += [f"- ❌ {k}" for k in fails]
            md.append(f"- raw plan: `eval/reports/raw/{args.label}/{tid}.txt`")
            md.append("")

    md_path = os.path.join(HERE, "reports", f"make-eval-{args.label}.md")
    open(md_path, "w").write("\n".join(md) + "\n")
    json_path = os.path.join(HERE, "reports", f"make-eval-{args.label}.json")
    open(json_path, "w").write(json.dumps(
        {"label": args.label, "model": model, "tasks": results}, indent=2))
    print(f"\nwrote {os.path.relpath(md_path, REPO)} and .json sidecar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
