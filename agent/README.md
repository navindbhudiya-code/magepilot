# Magepilot — AI agent for Magento 2

A **local, tool-using AI agent** that answers questions about a *specific* Magento 2 codebase by
**inspecting the real code** — not by guessing from memory. It indexes your project, then reasons in a
**think → act → observe** loop using sandboxed tools (semantic code search, grep, file reads, a curated
Magento knowledge base, and read-only `bin/magento`).

It is the agentic layer of the Magepilot stack:
- the **fine-tuned model + RAG** write idiomatic Magento code (style + facts);
- **this agent** investigates a real codebase and grounds every answer in what the tools actually return.

> Runs fully locally on Apple Silicon against your `mlx_lm.server` — no code leaves your machine.

## How it works

1. **Index** (`#1`) — `codebase_index.py` walks your codebase, splits files into overlapping
   line-windows, embeds them (MiniLM-ONNX, local/CPU) and stores them in ChromaDB.
2. **Tools** (`#2`) — `tools.py` exposes sandboxed actions the agent may take.
3. **Reason** (`#3`) — `react_agent.py` runs a ReAct loop: the model emits `Action` + `Action Input`,
   the tool runs, the `Observation` feeds back, and it iterates until a grounded `Final Answer`.

The agent's **reasoning model is the base `Qwen2.5-Coder-7B-Instruct`** (it follows the tool format
reliably); the Magento **fine-tune + RAG remain the code-generation/style layer**. Both are served by the
same local `mlx_lm.server` — the agent auto-selects the base from `/v1/models`.

## Install & setup

### Prerequisites
- **Apple Silicon Mac** (MLX-only, like the rest of the stack).
- The repo's Python venv at `mlx-env/` (managed by **`uv`** — it has no `pip`; use `uv pip install`).
- A running **`mlx_lm.server`** on `:8080` (via `serving/serve.sh`) serving the model(s). The agent
  reasons on the **base** `Qwen2.5-Coder-7B-Instruct`, which the same server exposes alongside the fine-tune.
- **ChromaDB** — the vector store for the code index + knowledge base (already the RAG layer's dependency).
- *Optional:* **ripgrep** (`brew install ripgrep`) for faster `grep` (falls back to pure-Python if absent).
- *For SQL debugging:* a host **`mysql` client**, **or** Docker / **Warden** (the agent runs the query
  inside your db container — no host client needed).

### Install
```bash
cd <repo-root>
source mlx-env/bin/activate
uv pip install chromadb          # only if the RAG layer isn't installed yet
# no agent-specific dependencies beyond chromadb — all HTTP uses the Python stdlib
```

### Setup (one-off)
```bash
# 1) start the local model server (serves the base Instruct model + the fine-tune)
bash serving/serve.sh                         # http://127.0.0.1:8080

# 2) (optional) build the knowledge base that `kb_search` uses
python rag/ingest.py

# 3) index the Magento codebase you want to work on (a store root, or one module)
python -m agent.cli index --root /path/to/magento
```
Each project's index is written to `~/.cache/magepilot/index/<hash-of-path>/` (outside the repo, so it's
per-project and shared across clones) and reused until you re-index. Override with `MAGEPILOT_CACHE`.

### Database access (optional — for the `sql` command / `sql_query` tool)
The agent reads DB credentials from `<root>/app/etc/env.php` and **auto-selects a transport**:

| Your setup | What to set |
|------------|-------------|
| Host `mysql` client reaching the DB | `AGENT_DB_HOST=127.0.0.1` + `AGENT_DB_PORT=<mapped-port>` |
| Docker (any) | `export AGENT_DB_CONTAINER=<db-container-name>`  (e.g. `mystore-db-1`) |
| **Warden** | nothing — it runs `warden db connect` from the project root automatically |

Force a specific transport with `AGENT_DB_MODE=host|docker|warden` (default `auto`). For Warden, the
db container is typically `<env-name>-db-1` (`docker ps` to confirm).

## Tools (all sandboxed to the codebase root)

| Tool | Purpose |
|------|---------|
| `search_code` | semantic search over the indexed codebase ("where/how is X done") |
| `grep` | exact/regex symbol search (ripgrep if present, else pure-Python) |
| `read_file` | read a file or line range — tolerant of guessed `app/code/` prefixes |
| `find_files` | locate files by name/glob |
| `list_dir` | list a directory |
| `kb_search` | look up Magento facts/APIs in the curated knowledge base |
| `magento_cli` | run a **read-only** whitelisted `bin/magento` command |
| `sql_query` | run a **read-only** SQL query against the store DB (debugging) |

## Safety

- **Filesystem sandbox.** Every file tool resolves paths *inside the codebase root*; path traversal
  (`../`, absolute paths) is refused. The agent cannot read files outside the project.
- **Read-only CLI.** `magento_cli` runs only a fixed whitelist (`module:status`, `dev:di:info`,
  `cache:status`, `indexer:status`, `setup:db:status`, `config:show`, `app:config:status`) and rejects
  shell metacharacters — it can never flush caches, run setup:upgrade, reindex, or shell out.
- **No writes, ever.** The agent only reads and reports; it does not edit files or change state.
- **Privacy.** The built index lives in `~/.cache/magepilot/` (outside the repo entirely) — your real
  code is never committed or uploaded.

## Usage

```bash
# 0) the local model server must be running (serves the base + the fine-tune)
serving/serve.sh                      # http://127.0.0.1:8080

# 1) index a Magento codebase (a store root, or a single module)
python -m agent.cli index --root /path/to/magento

# 2) ask about it (root defaults to the last-indexed one)
python -m agent.cli run "Where is the product-price plugin wired, and what does it add?"
python -m agent.cli run "Which method throws NoSuchEntityException, and on what condition?"
python -m agent.cli run --quiet "List the observers this module registers and their events."
```

`run` prints the reasoning trace (thought / action / observation) and the final answer; `--quiet`
prints only the answer.

## Acting on changes — approval-gated commands

Magepilot can watch what you changed and **propose the Magento commands those changes require**, then
run the ones you approve — like Claude Code's permission flow, but Magento-aware.

```bash
# propose commands for your uncommitted changes (git), approving each y / n / always
python -m agent.cli suggest --root /path/to/magento

python -m agent.cli suggest --root /path/to/magento --plan          # only propose, never run
python -m agent.cli watch   --root /path/to/magento                 # continuous, on every change
```

The change → command mapping is **rule-based and deterministic** (the model never invents a command):

| Changed | Proposed |
|---------|----------|
| `db_schema.xml` | `setup:db-declaration:generate-whitelist` → `setup:upgrade` |
| `module.xml` / `registration.php` | `setup:upgrade` |
| `Setup/**/*.php` (patches) | `setup:upgrade` |
| `indexer.xml` / `mview.xml` | `setup:upgrade` → `indexer:reindex` |
| `di.xml`, `events.xml`, observers, `system.xml`/`routes.xml`/`webapi.xml`/`*.graphqls`/… | `cache:clean config` |
| `*.phtml` / layout | `cache:clean block_html layout full_page` |
| `i18n/*.csv` | `cache:clean` |
| `composer.json` / `composer.lock` | `composer dump-autoload` |
| `requirejs-config.js`, `web/**`, `*.less` | `setup:static-content:deploy -f` |

Every command passes a **3-tier policy** before running:

| Tier | Commands | Behavior |
|------|----------|----------|
| **auto** | read-only (`*:status`, `dev:di:info`, `config:show`) | run without asking |
| **ask** | state-changing (`setup:upgrade`, `setup:di:compile`, `cache:clean/flush`, `indexer:reindex`, `setup:static-content:deploy`, `module:enable/disable`, …) | **prompt `y` / `n` / `always`** |
| **blocked** | anything else — non-whitelisted, destructive, or containing shell metacharacters | **refused** |

Only `php bin/magento <subcommand>` is ever executed — **never arbitrary shell**. `--auto-approve`
runs the `ask` tier without prompting (use with care); `--plan` never executes.

## Database debugging — read-only SQL

For DB-related issues, the agent can query the store database — and because the model knows Magento's
schema (`catalog_product_entity`, `cataloginventory_stock_item`, `sales_order`, `eav_attribute`,
`*_index`, `core_config_data`, …), it can investigate with the right tables.

```bash
python -m agent.cli sql --root /path/to/magento "SHOW TABLES LIKE 'catalog_product%'"
python -m agent.cli sql --root /path/to/magento "SELECT entity_id, sku FROM catalog_product_entity LIMIT 20"

# or inside an agent task — it will query the DB itself when relevant:
python -m agent.cli run "Why might product SKU ABC-1 not appear on the storefront?"
```

**Strictly read-only — this is the safety boundary:**
- Only `SELECT` / `SHOW` / `DESCRIBE` / `EXPLAIN` run. **Every write/DDL is refused** (INSERT, UPDATE,
  DELETE, DROP, ALTER, TRUNCATE, CREATE, REPLACE, GRANT), as are stacked statements (`;`) and file I/O
  (`INTO OUTFILE`, `LOAD_FILE`) — checked *before* any connection is made.
- Unbounded `SELECT`s get an automatic `LIMIT` (default 200).
- Credentials are read from `app/etc/env.php`; the password is passed via `MYSQL_PWD`, never on the
  command line or in logs.
- **Transport is auto-selected** (host `mysql` → Docker container → `warden db connect`). For Warden,
  just run from the project root; for plain Docker, set `AGENT_DB_CONTAINER`. See
  [Database access](#database-access-optional--for-the-sql-command--sql_query-tool) above.

Example (Warden store):
```bash
export AGENT_DB_CONTAINER=mystore-db-1     # or rely on `warden db connect` auto-detection
python -m agent.cli sql --root /path/to/store \
  "SELECT entity_id, sku FROM catalog_product_entity LIMIT 20"
```

## Testing

Deterministic tests run **without the model** against a synthetic fixture module — the sandbox, every
tool, the `bin/magento` + composer whitelists, the ReAct parser, indexing + semantic search, the
change→command mapping, and the **read-only SQL guard**:

```bash
python -m agent.tests.run_tests      # 28 tests, no model required
```

## Capabilities & limits (honest)

- **Solid:** locating code, reading exact lines, tracing "where/what/why" across a module, and grounding
  answers in real file paths + line numbers. The framework is sandboxed and test-covered.
- **Bounded by a local 7B:** reasoning is good but not GPT-4-level. On hard multi-file tasks it can take
  extra steps or be less precise; the loop has guards (force-investigate, loop-break, step cap) so it
  degrades gracefully rather than hallucinating. For best results, ask focused questions.
- **Apple Silicon / MLX only**, like the rest of the stack.

## Configuration

All knobs live in `agent/config.py`; the common ones have environment overrides:

| Env var | Default | Purpose |
|---------|---------|---------|
| `AGENT_CODEBASE` | (last indexed) | default codebase root, so you can omit `--root` |
| `MODEL_SERVER` | `http://127.0.0.1:8080/v1` | the OpenAI-compatible model server |
| `AGENT_MODEL` | (auto = base Instruct) | force the reasoning model id |
| `AGENT_DB_MODE` | `auto` | DB transport: `host` / `docker` / `warden` |
| `AGENT_DB_CONTAINER` | — | db container name for Docker mode |
| `AGENT_DB_HOST` / `AGENT_DB_PORT` | from `env.php` | override DB host/port (host mode) |

In `config.py` (no env override): `MAX_STEPS`, `MAX_OBS_CHARS`, `TOP_K_CODE`/`TOP_K_KB`, chunking
(`CHUNK_LINES`/`CHUNK_OVERLAP`), `INDEX_EXTENSIONS`/`SKIP_DIRS`, `DB_ROW_LIMIT`, and the
`MAGENTO_CLI_WHITELIST`.

## Command reference

| Command | What it does |
|---------|--------------|
| `python -m agent.cli index --root <path>` | build/refresh the codebase index |
| `python -m agent.cli run "<task>"` | ask the agent (add `--quiet` for answer-only) |
| `python -m agent.cli suggest --root <path>` | propose commands for your git changes (`--plan`, `--auto-approve`) |
| `python -m agent.cli watch --root <path>` | continuously propose as files change |
| `python -m agent.cli sql --root <path> "<SELECT…>"` | run a read-only query |
| `python -m agent.tests.run_tests` | run the deterministic test suite |
