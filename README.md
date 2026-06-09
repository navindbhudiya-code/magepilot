<p align="center">
  <img src="docs/banner.svg" alt="MAGEPILOT" width="680">
</p>

<h1 align="center">Magepilot 🧙</h1>

<p align="center"><b>A local, self-hostable AI coding agent for Magento&nbsp;2 &amp; Hyvä</b><br/>
<sub>writes idiomatic code · inspects your real codebase · creates &amp; edits files (you approve each) · runs <code>bin/magento</code> · debugs the DB — all on your machine</sub></p>

<p align="center">
  <a href="https://huggingface.co/navindbhudiya/qwen2.5-coder-7b-magento-v2"><img alt="model" src="https://img.shields.io/badge/%F0%9F%A4%97%20model-Qwen2.5--Coder--Magento-yellow"></a>
  <img alt="platform" src="https://img.shields.io/badge/Apple%20Silicon-MLX-black?logo=apple&logoColor=white">
  <img alt="stack" src="https://img.shields.io/badge/Magento%202-Hyv%C3%A4-f46f25">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-3fb950">
  <a href="https://in.linkedin.com/in/navindbhudiya"><img alt="built by" src="https://img.shields.io/badge/built%20by-Navin%20D.%20Bhudiya-8957e5"></a>
</p>

---

A **private, locally-running AI coding agent** for **Magento 2 + Hyvä** (PHP 8, Alpine.js, Tailwind).
From one interactive shell it answers questions, **inspects your real codebase**, **creates and edits files**
(with per-change approval), **runs the right `bin/magento` commands**, and **debugs the database** — all on
your machine, like a Magento-native `claude`. **No code leaves your Mac.**

---

## What it does

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
- 🖥️ **Interactive or scriptable** — run `magepilot` in any project folder (the current directory *is* the
  project), or use any command directly (`magepilot make …`, `magepilot sql …`).
- 🔒 **Safe & private** — every write and command is **approval-gated and sandboxed** to the project; the
  model, index, and DB access all run locally; nothing is uploaded.

---

## Requirements

- **Apple Silicon Mac (M1–M4)** — required for the **bundled** setup (the MLX model + `mlx_lm.server`).
  MLX runs only on Apple Silicon, and the model is published in MLX format. *Other platforms: see below.*
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
3. Then use `python -m agent.cli …` and `python rag/ask.py …` exactly as in **Use it** below.

> **Note on the fine-tune:** the published weights are **MLX-only** for now, so on other platforms you run
> the **base Qwen2.5-Coder**. You keep the full **agent** (it reasons on the base model anyway) and **RAG**
> facts; you lose only the Magento *style* fine-tune until the weights are converted to GGUF/HF format.
> WSL2 is Linux — MLX won't run inside it, but serving via Ollama/llama.cpp + the RAG/agent works there.

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

brew install uv             # if you don't have it
./magepilot install         # env + deps + knowledge base, and adds `magepilot` to your PATH
```

Open a new terminal afterwards so `magepilot` is picked up. Then you can run it from any Magento project.

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
magepilot make "a Hyva theme Demo with parent Hyva/default"   # creates files (use --plan to preview only)
magepilot undo                        # revert the last make
magepilot index                       # index the current project
magepilot run "Which class throws NoSuchEntityException, and why?"
magepilot suggest                     # commands for your uncommitted changes
magepilot sql "SELECT entity_id, sku FROM catalog_product_entity LIMIT 20"
```

> **Run `magepilot` from anywhere** — put it on your PATH once (from the repo):
> ```bash
> mkdir -p ~/.local/bin && ln -s "$(pwd)/magepilot" ~/.local/bin/magepilot
> ```
> Make sure `~/.local/bin` is on your `PATH` (or symlink into `/usr/local/bin` with `sudo`), then open a
> new terminal. Now `cd` into any Magento project and just run `magepilot`.

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
- **More agent detail** (every tool, the safety model, configuration env vars): see
  [`agent/README.md`](agent/README.md). RAG details: [`rag/README.md`](rag/README.md).

## Privacy

Your code, the index the agent builds (in `~/.cache/magepilot/`), and your database stay **local** — nothing
is uploaded. The model itself was trained only on anonymized, generic Magento patterns.

## Author & license

Built by **[Navin D. Bhudiya](https://in.linkedin.com/in/navindbhudiya)** — AI Engineer (11+ years in
e-commerce at scale; production RAG, LLM agents, and intelligent search on AWS; AWS + Anthropic certified).

The Magepilot code is released under the **MIT License** — see [LICENSE](LICENSE). The model
is fine-tuned from **Qwen2.5-Coder-7B-Instruct** (Apache-2.0).