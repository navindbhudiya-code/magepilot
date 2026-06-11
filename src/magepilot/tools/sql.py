"""Read-only SQL tool — wraps magepilot.magento.db (migrated verbatim from agent/tools.py)."""
from magepilot.tools.base import Param, RiskLevel, Tool, clip as _clip, root_tool


def sql_query(root: str, query: str) -> str:
    """Run a READ-ONLY SQL query against the store DB. Writes/DDL are refused."""
    from magepilot.magento.db import run_query
    r = run_query(root, query)
    return _clip(r["output"]) if r["ok"] else f"error: {r['error']}"


TOOLS = (
    Tool(
        name="sql_query", fn=root_tool(sql_query), primary="query", risk=RiskLevel.READ,
        params=(Param("query", required=True, description="a single SELECT/SHOW/DESCRIBE/EXPLAIN"),),
        description="Run a READ-ONLY SQL query against the store database for debugging "
                    "(SELECT / SHOW / DESCRIBE only — writes refused). Input: a SQL string. Use your "
                    "knowledge of Magento's schema (catalog_product_entity, sales_order, eav_attribute, "
                    "*_index tables, core_config_data, etc.).",
    ),
)
