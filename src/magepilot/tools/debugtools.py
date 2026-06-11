"""Debugging tools: stack_trace (parse a pasted PHP error) and magento_logs (tail the
store's var/log files). Both READ tier — pure inspection."""
from magepilot.debug.stacktrace import analyze, recent_log_entries
from magepilot.tools.base import Param, RiskLevel, Tool, root_tool

TOOLS = (
    Tool(
        name="stack_trace", fn=root_tool(analyze), primary="trace", risk=RiskLevel.READ,
        params=(Param("trace", required=True, description="the full PHP error text incl. #0 … frames"),),
        description="Parse a PHP error/stack trace: exception class, message, every frame "
                    "relativized to this project, the most likely culprit flagged "
                    "(app/code beats vendor), and a DI/plugin hint when generation or "
                    "interception is involved. ALWAYS run this first when given an error.",
    ),
    Tool(
        name="magento_logs", fn=root_tool(recent_log_entries), primary="log", risk=RiskLevel.READ,
        params=(Param("log", description="exception.log | system.log | debug.log | cron.log"),
                Param("n", type="integer", description="number of recent entries (default 2)")),
        description="Read the LAST entries of the store's var/log files. Use when the user "
                    "reports an error without pasting it — the trace is usually in "
                    "exception.log.",
    ),
)
