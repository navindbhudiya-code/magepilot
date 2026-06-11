"""Memory tools: `remember` persists a durable PROJECT fact mid-task (global memory is
never written by the agent — explicit user action only); `recall_memory` is the pull
side of recall for deeper lookup than the injected Known-facts block."""
from magepilot.memory.store import project_store
from magepilot.tools.base import Param, RiskLevel, Tool, ToolContext


def remember(ctx: ToolContext, fact: str, kind: str = "fact") -> str:
    store = project_store(ctx.root)
    try:
        fid = store.add(fact, kind=kind if kind in
                        ("fact", "file_role", "decision", "gotcha") else "fact",
                        source="remember")
    finally:
        store.close()
    return "remembered" if fid else "nothing to remember (empty fact)"


def recall_memory(ctx: ToolContext, query: str) -> str:
    store = project_store(ctx.root)
    try:
        rows = store.search(query, k=6)
        store.touch([r["id"] for r in rows])
    finally:
        store.close()
    if not rows:
        return "no remembered facts match — investigate with the other tools"
    return "\n".join(f"- [{r['kind']}] {r['content']}"
                     + (f" (source: {r['source']})" if r["source"] else "") for r in rows)


TOOLS = (
    Tool(
        name="remember", fn=remember, primary="fact", risk=RiskLevel.READ,
        params=(Param("fact", required=True,
                      description="one durable, verified fact worth keeping (include file paths)"),
                Param("kind", description="fact | file_role | decision | gotcha")),
        description="Save a durable fact about THIS project to memory so future sessions "
                    "don't rediscover it (e.g. 'checkout totals are collected in "
                    "app/code/Vendor/X/Model/Total/Fee.php'). Only save VERIFIED facts.",
    ),
    Tool(
        name="recall_memory", fn=recall_memory, primary="query", risk=RiskLevel.READ,
        params=(Param("query", required=True, description="what to look up"),),
        description="Search facts remembered from earlier sessions about THIS project. "
                    "Cheaper than re-investigating — but verify any cited path still exists.",
    ),
)
