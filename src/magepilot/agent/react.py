"""The agent loop — a ReAct controller (Reason + Act). Migrated from agent/react_agent.py:
the loop and every guard are unchanged; model access goes through the role router
(role "executor" — the base Instruct model in the default local setup) and tool dispatch
goes through the registry.

The model is prompted to emit, each turn, either:

    Thought: <reasoning>
    Action: <tool name>
    Action Input: <json object or plain string>

…on which we run the tool and feed back `Observation: <result>`; or:

    Thought: <reasoning>
    Final Answer: <answer to the user>

We stop generation at "Observation:" so the model can't hallucinate tool output, run the
real tool, and continue. The loop is bounded by MAX_STEPS and degrades gracefully on
malformed output (one nudge, then it returns whatever it has).
"""
import urllib.error

from magepilot import config
from magepilot.agent import compress
from magepilot.llm.router import get_router
from magepilot.tools import run_tool, tool_catalog
from magepilot.tools.parsing import _ACTION_RE, _FINAL_RE, _first_arg  # noqa: F401 (v1 names)


def _system_prompt(tools_subset: tuple | None = None) -> str:
    return f"""You are Magepilot, an AI agent for Magento 2. You answer questions about a specific \
Magento codebase by USING TOOLS to inspect it — never guess at file contents or APIs.

You have these tools:
{tool_catalog(tools_subset)}

Work in this exact loop, one step at a time:

Thought: what you need to find out next
Action: one tool name from the list above
Action Input: the input for that tool (a plain string, or a JSON object for read_file)

After each Action you will be shown an "Observation:" with the tool's result. Use it to decide \
the next step. When you have enough information, finish with:

Thought: I now know the answer
Final Answer: <a clear, correct answer for the developer, citing file paths and line numbers>

Rules (critical):
- NEVER answer from memory. This is a SPECIFIC codebase — any file path, class, or line you recall \
without a tool WILL be wrong. You MUST use at least one tool before a Final Answer.
- Output ONLY one Thought + one Action + one Action Input per step (or the Final Answer). Do not \
write "Observation:" yourself — that is given to you.
- Prefer search_code / find_files / grep to locate code, then read_file to confirm exact details.
- search_code only sees THIS project's indexed app/code — it does NOT see vendor/. To find a core or \
third-party Magento class/interface (anything `Magento\\...`, or "which class implements X"), use grep \
or find_files — they search vendor/ too. Magento writes fully-qualified names, so grep the bare symbol \
or use `.*` (e.g. 'class .*ProductRepository' or 'implements .*ProductRepositoryInterface'), NOT \
'implements ProductRepositoryInterface'. If search_code returns nothing useful, switch to grep; never \
repeat the same search_code call.
- For Magento facts/APIs you are unsure about, use kb_search before answering.
- Cite only file paths and line numbers that appeared in an Observation. If the tools don't show \
something, say so rather than inventing it.
- Be efficient and decisive: as soon as an Observation shows you the answer, STOP and give the \
Final Answer — do not keep investigating. Cite the EXACT line of the specific statement (e.g. the \
line of the `throw`), not a whole-file range, and name the exact method it is in."""


def _example() -> str:
    # A one-shot exemplar keeps a 7B model on-format. Labeled explicitly so the model
    # doesn't execute the example call as its own first step (it did, before Phase 4).
    return (
        "(Format example only — this is NOT your task, choose your own first action:\n"
        "Thought: I should locate where the relevant code lives.\n"
        "Action: search_code\n"
        "Action Input: <your own search terms>\n"
        ")\n"
    )


def call_model(messages: list[dict], stop: list[str]) -> str:
    return get_router().complete("executor", messages, stop=stop, sampling=config.SAMPLING)


def run(task: str, root: str, max_steps: int = None, verbose: bool = True, *,
        system_addendum: str = "", initial_scratchpad: str = "",
        on_step=None, cancel=None, tools_subset: tuple | None = None) -> dict:
    """Run the agent on `task` against the codebase at `root`.

    Orchestrator hooks (all optional — v1 callers are unaffected):
      system_addendum     extra system-prompt text (task goal/done_when, prior task notes)
      initial_scratchpad  resume a checkpointed mid-task scratchpad
      on_step(scratchpad, n_steps)  called after every tool step (checkpointing)
      cancel              threading.Event-like; set → stop cleanly with stopped="cancelled"

    Returns {"answer", "steps": [{thought, action, action_input, observation}],
             "stopped", "scratchpad"}.
    """
    max_steps = max_steps or config.MAX_STEPS
    scratchpad = initial_scratchpad
    steps = []
    seen_calls = set()
    nudged = False
    rejected_finals = 0
    repeat_strikes = 0
    system = _system_prompt(tools_subset) + (f"\n\n{system_addendum}" if system_addendum else "")

    for _ in range(max_steps):
        if cancel is not None and cancel.is_set():
            return {"answer": "", "steps": steps, "stopped": "cancelled", "scratchpad": scratchpad}
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Question: {task}\n\nBegin.\n\n{_example() if not scratchpad else ''}{scratchpad}"},
        ]
        out = call_model(messages, stop=["Observation:", "<|im_end|>"]).strip()

        final = _FINAL_RE.search(out)
        if final:
            # Refuse to accept an answer that used no tools — it is answering from (wrong) memory.
            if not steps and rejected_finals < 2:
                rejected_finals += 1
                scratchpad += (
                    "(You answered without inspecting the codebase. That answer is rejected — your "
                    "memory of file paths/classes WILL be wrong for this specific project. Start over: "
                    "emit an `Action:` to investigate the real code first.)\n"
                )
                if verbose:
                    print("\n\033[91m[guard]\033[0m rejected a no-tool Final Answer — forcing investigation.")
                continue
            answer = final.group(1).strip()
            if verbose:
                print(f"\n\033[92mFinal Answer:\033[0m {answer}")
            return {"answer": answer, "steps": steps, "stopped": "final", "scratchpad": scratchpad}

        m = _ACTION_RE.search(out)
        if not m:
            if not nudged:
                nudged = True
                scratchpad += out + "\n(Reminder: respond with `Action:` + `Action Input:`, or `Final Answer:`.)\n"
                continue
            return {"answer": out.strip(), "steps": steps, "stopped": "unparsed", "scratchpad": scratchpad}

        thought = out[:m.start()].replace("Thought:", "").strip()
        action = m.group(1).strip().strip("`")
        action_input = m.group(2).strip()
        # Action Input may have eaten trailing text; keep only its first line/JSON.
        action_input = _first_arg(action_input)

        sig = f"{action}|{action_input}"
        if sig in seen_calls:
            repeat_strikes += 1
            if repeat_strikes >= 3:
                if verbose:
                    print("\n\033[91m[guard]\033[0m repeated the same call 3x — synthesizing from findings.")
                break
            observation = ("You already ran this exact call — its result is above. Do something "
                           "DIFFERENT: use read_file to open the full file and see the relevant lines, "
                           "use grep for the exact symbol, or give your Final Answer now.")
        else:
            repeat_strikes = 0
            seen_calls.add(sig)
            observation = run_tool(root, action, action_input)
        steps.append({"thought": thought, "action": action,
                      "action_input": action_input, "observation": observation})
        if verbose:
            print(f"\n\033[96mThought:\033[0m {thought}")
            print(f"\033[93mAction:\033[0m {action}  \033[93mInput:\033[0m {action_input}")
            print(f"\033[90mObservation:\033[0m {observation[:400]}{'…' if len(observation) > 400 else ''}")

        scratchpad += (f"Thought: {thought}\nAction: {action}\nAction Input: {action_input}\n"
                       f"Observation: {observation}\n")
        if len(scratchpad) > compress.FOLD_THRESHOLD:
            scratchpad = compress.fold_scratchpad(scratchpad)
        if on_step is not None:
            on_step(scratchpad, len(steps))

    # Out of steps — ask the model to summarize what it found.
    summary = _force_answer(task, scratchpad)
    return {"answer": summary, "steps": steps, "stopped": "max_steps", "scratchpad": scratchpad}


def _force_answer(task: str, scratchpad: str) -> str:
    """Final synthesis when the step budget is exhausted."""
    messages = [
        {"role": "system", "content": "You are Magepilot. Using ONLY the investigation notes below, "
                                       "give the developer a clear, correct final answer with file paths "
                                       "and line numbers. If the notes are insufficient, say what is missing."},
        {"role": "user", "content": f"Question: {task}\n\nInvestigation notes:\n{scratchpad}\n\nFinal Answer:"},
    ]
    try:
        return call_model(messages, stop=["<|im_end|>"]).strip()
    except (urllib.error.URLError, OSError) as e:
        return f"(agent reached the step limit; model summary unavailable: {e})"
