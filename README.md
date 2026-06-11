<p align="center">
  <img src="docs/banner.svg" alt="MAGEPILOT" width="680">
</p>

<h1 align="center">Magepilot 🧙</h1>

<p align="center"><b>A local, self-hostable AI coding agent for Magento&nbsp;2 &amp; Hyvä</b><br/>
<sub>plans &amp; executes multi-step objectives · knows your DI/plugin/observer wiring exactly · writes idiomatic code · creates &amp; edits files (you approve each) · debugs stack traces, git history &amp; the DB — all on your machine</sub></p>

<p align="center">
  <a href="https://huggingface.co/navindbhudiya/qwen2.5-coder-7b-magento-v2"><img alt="model" src="https://img.shields.io/badge/%F0%9F%A4%97%20model-Qwen2.5--Coder--Magento-yellow"></a>
  <img alt="bundled platform" src="https://img.shields.io/badge/Apple%20Silicon-MLX-black?logo=apple&logoColor=white">
  <img alt="also runs on" src="https://img.shields.io/badge/Linux%20%C2%B7%20Windows%20%C2%B7%20WSL-via%20Ollama-2496ed">
  <img alt="stack" src="https://img.shields.io/badge/Magento%202-Hyv%C3%A4-f46f25">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-3fb950"></a>
  <a href="https://huggingface.co/navindbhudiya/qwen2.5-coder-7b-magento-v2"><img alt="model license" src="https://img.shields.io/badge/model%20license-Apache--2.0-blue"></a>
  <a href="https://navindbhudiya.com/"><img alt="built by" src="https://img.shields.io/badge/built%20by-Navin%20D.%20Bhudiya-8957e5"></a>
</p>

---

A **private, locally-running AI coding agent** for **Magento 2 + Hyvä** (PHP 8, Alpine.js, Tailwind).
From one interactive shell it answers questions, **inspects your real codebase**, **creates and edits files**
(with per-change approval), **runs the right `bin/magento` commands**, and **debugs the database** — all on
your machine, like a Magento-native `claude`. **No code leaves your machine.**

---

## Install

### Quick install

```bash
curl -fsSL https://navindbhudiya.com/install.sh | bash
```

That's it. The installer sets up `uv`, clones Magepilot to `~/.magepilot`, builds the Python
environment + Magento knowledge base, and adds the `magepilot` command to your PATH. The
~4 GB model is **not** downloaded here — that happens on your first `magepilot serve`.

> **Inspect it first?** `curl -fsSL https://navindbhudiya.com/install.sh | less` prints the
> script before you run anything. Override the location with
> `MAGEPILOT_HOME=/path curl -fsSL https://navindbhudiya.com/install.sh | bash`.

Re-running the one-liner later updates an existing install (`git pull`).

### Manual install

```bash
git clone https://github.com/navindbhudiya-code/magepilot.git
cd magepilot

brew install uv                                 # macOS — if you don't have it
# Linux / WSL: curl -LsSf https://astral.sh/uv/install.sh | sh
./magepilot install         # env + deps + knowledge base, and adds `magepilot` to your PATH
```

Open a new terminal afterwards so `magepilot` is picked up. Then you can run it from any Magento project.

## What it does

- 🤖 **Executes multi-step objectives autonomously** — `magepilot do "create a Vendor_Faq module with an
  admin grid"` plans the tasks (investigate → edit → command → verify), runs them with per-change approval,
  and **checkpoints every step**: Ctrl-C pauses, `magepilot resume` continues exactly where it left off,
  `magepilot runs` lists past runs.
- 🕸️ **Knows your Magento wiring exactly** — `magepilot graph` builds a **knowledge graph** of your whole
  store (DI preferences with the real load-order winner, plugin chains with sortOrder/area/disabled,
  observers + dispatch sites, constructor injections, REST routes, GraphQL resolvers, layout/templates/
  view models, db_schema tables, cron jobs, a static call graph of your `app/code`, and even **Hyvä Alpine
  components + browser CustomEvents** — vendor/ included, ~40–80s for a full 2.4 store). The agent then
  answers *"what plugins intercept this method?"*, *"which service handles `GET /V1/products/:sku`?"*,
  *"who listens to `private-content-loaded`?"*, *"what breaks if I change this interface — and is it
  tested?"* from facts, not grep — including cross-module disables (the MSI-style case text search can
  never find).
- 🐛 **Debugs from a stack trace** — paste a PHP error into `magepilot do` and it parses the frames, flags
  the culprit (your code before vendor code), reads `var/log/*.log`, root-causes DI/plugin failures via the
  graph, and proposes the smallest fix.
- 💬 **Grounded answers** — idiomatic Magento 2 + Hyvä code, with facts grounded by a knowledge base (RAG)
  so it doesn't make up APIs.
- 🔍 **Understands your codebase** — `/index` builds a **per-project** semantic index; `/code` answers from
  your real files (not from memory).
- ✍️ **Creates & edits files** — `/make` scaffolds modules/themes/plugins and edits existing files,
  **previewing every change and asking before writing** (`y`/`n`/`all`). Asks for missing details (e.g. the
  vendor name), and edits in place with indentation-aware diffs.
- ↩️ **Undo** — `/undo` reverts the last `make` exactly: the files **and** the empty dirs it created — and
  never touches `generated/`, `vendor/`, `var/`, `pub/`, or `app/code` itself.
- ⚙️ **Runs Magento commands (with output)** — `/suggest` maps your git changes to the right `bin/magento`
  commands and runs the ones you approve; `make` offers to run follow-ups like `setup:upgrade`.
- 🗄️ **Read-only DB debugging** — `/sql` queries the store database (Warden / Docker auto-detected); every
  write is refused.
- 🧹 **A Magento linter guards every write** — 15 deterministic rules (no `ObjectManager`, no SQL
  concatenation, no secrets, escaped output, no loads-in-loops, …) run **before** you're even asked to
  approve; rule-breaking code never reaches disk, and `vendor/`, `generated/`, `app/etc/env.php` are
  write-blocked outright. `magepilot review` runs an advisory AI review over your uncommitted diff.
- 🌿 **Git-aware** — the agent reads status/diff/log/blame to understand recent changes, and can branch +
  commit **with your approval**. There is deliberately **no push capability**: nothing leaves your machine.
- 🧠 **Remembers your project** — durable facts from each session (file roles, decisions, gotchas) are
  recalled in the next one, so the agent doesn't rediscover your architecture every time.
- 🖥️ **Interactive or scriptable** — run `magepilot` in any project folder (the current directory *is* the
  project), or use any command directly (`magepilot make …`, `magepilot sql …`).
- 🔒 **Safe & private** — every write and command is **approval-gated and sandboxed** to the project; the
  model, index, and DB access all run locally; nothing is uploaded.

---

## Requirements

- **A machine to run the model on** — either:
  - **Apple Silicon Mac (M1–M4)** for the **bundled** one-command setup (the MLX model + `mlx_lm.server`), or
  - **any Linux / Windows / WSL box** that can serve `Qwen2.5-Coder-7B` via Ollama/llama.cpp/vLLM —
    the agent and RAG layers are pure-Python and cross-platform. *See [Running on Linux / Windows / WSL](#running-on-linux--windows--wsl).*
- **[`uv`](https://docs.astral.sh/uv/)** (Python package manager): `brew install uv` — *the
  [quick installer](#quick-install) sets this up for you automatically.*
- ~**5 GB** free disk (the model downloads once).
- *Optional:* PhpStorm + the **Continue** plugin · **ripgrep** (`brew install ripgrep`) for faster search ·
  **Docker/Warden** for the agent's database debugging.

### Running on Linux / Windows / WSL

Only the bundled **model server** needs Apple Silicon — the **RAG layer and the agent are cross-platform**
(pure Python). To run elsewhere, serve a model yourself and point the stack at it:

1. **Serve `Qwen2.5-Coder-7B-Instruct`** via **Ollama**, **llama.cpp**, or **vLLM** — any OpenAI-compatible
   endpoint works. e.g. with Ollama: `ollama pull qwen2.5-coder:7b` → API at `http://localhost:11434/v1`.
2. **Point the stack at it:**
   - **Agent:** `export MODEL_SERVER=http://localhost:11434/v1` and `export AGENT_MODEL=qwen2.5-coder:7b`
     (use whatever id your server reports at `/v1/models`).
   - **RAG:** set `MODEL_SERVER` in `rag/config.py` to the same URL.
3. Then use `python -m magepilot …` and `python rag/ask.py …` exactly as in **Use it** below.

> **Note on the fine-tune:** the published weights are **MLX-only** for now, so on other platforms you run
> the **base Qwen2.5-Coder**. You keep the full **agent** (it reasons on the base model anyway) and **RAG**
> facts; you lose only the Magento *style* fine-tune until the weights are converted to GGUF/HF format.
> WSL2 is Linux — MLX won't run inside it, but serving via Ollama/llama.cpp + the RAG/agent works there.

## Start it

```bash
./magepilot serve           # starts the model server (:8080) + RAG proxy (:8090)
                            # first run downloads the model (~4 GB) — progress in logs/model.log
```

`./magepilot status` shows what's running · `./magepilot stop` stops it.

---

## Use it

**`cd` into your Magento project and run `magepilot`** — it treats the current directory as the project to
work on (no `--root` needed). With no arguments you get the **interactive assistant** (like `claude`):

```text
$ cd ~/PhpstormProjects/my-store
$ magepilot
Magepilot — Magento 2 AI assistant.  /help for commands, /exit to quit.
codebase: ~/PhpstormProjects/my-store              # ← the directory you launched from
magepilot> add an extra charge to a product's price with a plugin
… grounded answer …
magepilot> /make a Hyva theme Demo with parent Hyva/default   # creates the files — you approve each
magepilot> /index                                  # one-off: index THIS store so /code can search it
magepilot> /code which plugin changes the product price?   # the agent inspects your real code
magepilot> /suggest                                # propose the Magento commands your edits need
magepilot> /sql SELECT sku FROM catalog_product_entity LIMIT 5
magepilot> /help        # every command            /exit
```

| Inside the shell | What it does |
|------------------|--------------|
| *just type a question* | grounded Magento answer (RAG + model) |
| `/do <objective>` | **plan + execute a multi-step objective** — approve each change; pause/resume any time |
| `/resume` · `/runs` | continue a paused/interrupted run · list recent runs |
| `/make <task>` | **create/edit files** for a task — previews each change, you approve `y`/`n`/`all` |
| `/undo` | **revert the last `/make`** — restores every file it touched |
| `/index` | index the current project so `/code` can search it (re-run after big changes) |
| `/code <task>` | the agent inspects your real code to answer |
| `/suggest` · `/watch` | propose & run the Magento commands your changes need (you approve `y`/`n`/`always`) |
| `/sql <SELECT…>` | read-only query against the store DB |
| `/use <path>` | switch to a different project |
| `/serve` · `/stop` · `/status` · `/install` | manage the servers / setup |

> **Discoverable:** type `/` (or press **Tab** after `/`) to see every command. Long steps — indexing and
> the model "thinking" — show **live progress**.

> `/sql` and `/suggest` work right away; only `/code` needs `/index` first (it builds a search index of your code).
> **Each project directory keeps its own index** — use `magepilot` in any folder and `/code` searches *that*
> project (you don't re-index when switching projects).

> **Creating/editing files:** type a request like *"create a module/theme/plugin …"* (or use `/make`) and
> Magepilot proposes each file change with a preview and **asks before writing** (`y` / `n` / `all`).
> It also **asks for missing details** (e.g. *"Vendor name?"* if you didn't give one) and **edits existing
> files in place** (indentation-aware diffs). After writing, it **offers to run the follow-up `bin/magento`
> commands** (e.g. `setup:upgrade`) and shows their output. Nothing is created, edited, deleted, or run
> without your approval — everything is sandboxed to the project.

Every command also works **non-interactively** — run from the project directory (or pass `--root <path>`):

```bash
cd ~/PhpstormProjects/my-store
magepilot ask "When do I use a plugin vs a preference?"
magepilot do "create a Vendor_Faq module with an admin grid"  # plan + execute, approving each change
magepilot resume                      # continue a paused/interrupted run (also: magepilot runs)
magepilot graph                       # build the Magento knowledge graph (DI/plugins/observers, incl. vendor/)
magepilot make "a Hyva theme Demo with parent Hyva/default"   # creates files (use --plan to preview only)
magepilot undo                        # revert the last make
magepilot index                       # index the current project
magepilot run "Which class throws NoSuchEntityException, and why?"
magepilot review                      # advisory AI review of your uncommitted diff
magepilot testgen 'Vendor\Faq\Model\FaqRepository'         # PHPUnit test (ctor mocks from the graph)
magepilot testgen initFaqList --kind playwright             # Playwright spec from an Alpine component
magepilot testgen faq_index_index --kind mftf               # MFTF Page/Section/Test scaffold
magepilot suggest                     # commands for your uncommitted changes
magepilot sql "SELECT entity_id, sku FROM catalog_product_entity LIMIT 20"
```

> **Run `magepilot` from anywhere** — put it on your PATH once (from the repo):
> ```bash
> mkdir -p ~/.local/bin && ln -s "$(pwd)/magepilot" ~/.local/bin/magepilot
> ```
> Make sure `~/.local/bin` is on your `PATH` (or symlink into `/usr/local/bin` with `sudo`), then open a
> new terminal. Now `cd` into any Magento project and just run `magepilot`.

### In Claude Code (or any MCP client)

Magepilot is also an **MCP server** — give Claude Code (Cursor, Zed, …) exact Magento
answers from your store's knowledge graph instead of letting it grep vendor/:

```bash
claude mcp add magepilot -- ~/.magepilot/magepilot mcp-serve --root /path/to/your-store
```

That exposes all the **read-only** tools (`wiring`, `symbol`, `impact`, `diagnose_plugin`,
`search`, `stack_trace`, `git_log`, `sql_query`, …) over stdio. Claude Code can then ask
*"what plugins intercept `ProductRepository::save`?"*, *"which service handles
`GET /V1/products/:sku`?"*, or *"who calls this method, and is it unit-tested?"* and get
graph facts, not guesses. Run `magepilot graph` in the
store once first. Write tools are **excluded by default**; opt in with
`mcp-serve --allow-writes` (your MCP client's permission prompts then gate them —
Magepilot's sandbox, linter, and undo still apply).

It works the other way too: declare external **MCP servers** in `~/.magepilot/config.toml`
and their tools join Magepilot's agent behind the same approval gate:

```toml
[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
# read_only = true   # opt-in: skip the approval prompt for this server's tools
```

### In PhpStorm
1. **Settings → Plugins →** install **Continue**, then restart the IDE.
2. `cp serving/phpstorm-continue.yaml ~/.continue/config.yaml`
3. Pick **Magento 2 chat (RAG)** — chat → `:8090` (grounded), autocomplete → `:8080`.

### Database access (for `/sql`)
Read-only only (`SELECT` / `SHOW` / `DESCRIBE`; **writes refused**). Credentials come from `app/etc/env.php`.
- **Warden:** run from the project root — it connects automatically.
- **Docker:** `export AGENT_DB_CONTAINER=<your-db-container>` (find it with `docker ps`).
- **Direct mysql:** `export AGENT_DB_HOST=127.0.0.1 AGENT_DB_PORT=<port>`.

---

## Tips & troubleshooting

- **Always keep the server running** before using `ask`, the agent, or PhpStorm.
- **Use `uv pip install`**, not `pip` (this environment has no `pip`).
- **Answers loop / repeat?** Make sure you're sampling (`--temp 0.3`), not greedy decoding — it's baked
  into the commands above.
- **The bundled model is Apple Silicon only** (MLX). The RAG layer + agent run on Linux/Windows/WSL too —
  see [Running on Linux / Windows / WSL](#running-on-linux--windows--wsl).
- **Model & provider config:** roles (planner/executor/coder/reviewer) and endpoints live in
  `~/.magepilot/config.toml` — fully local by default; remote providers are opt-in per role and never
  reachable implicitly. RAG details: [`rag/README.md`](rag/README.md).

## Privacy

Your code, the index the agent builds (in `~/.cache/magepilot/`), and your database stay **local** — nothing
is uploaded. The model itself was trained only on anonymized, generic Magento patterns.

## Author & license

Built by **[Navin D. Bhudiya](https://navindbhudiya.com/)** — AI Engineer (11+ years in
e-commerce at scale; production RAG, LLM agents, and intelligent search on AWS; AWS + Anthropic certified).

The Magepilot code is released under the **MIT License** — see [LICENSE](LICENSE). The model
is fine-tuned from **Qwen2.5-Coder-7B-Instruct** (Apache-2.0).