# RAG layer — factual grounding for the Magento assistant

The fine-tuned model supplies **style** (idiomatic Magento 2 / Hyvä). This layer supplies **facts**:
it retrieves authoritative Magento knowledge and injects it into the prompt, so the assistant stops
confabulating (e.g. it correctly describes a "preference" as a DI class replacement, not a setting).

```
question ─► retrieve top-K facts (ChromaDB) ─► inject as context ─► local model (mlx_lm.server) ─► grounded answer
            rag/knowledge/*.md                  system message       :8080 (sampling)
```

## Components
| File | Role |
|------|------|
| `knowledge/*.md` | the corpus — **generic** Magento facts (markdown, `##`-sectioned) |
| `config.py` | endpoints, model match, chroma path, top-k, sampling defaults |
| `ingest.py` | chunk `knowledge/` by `##` → embed → ChromaDB (`.chroma/`, gitignored) |
| `retriever.py` | semantic search → top-k chunks |
| `ask.py` | CLI: question → retrieve → local model → grounded answer (`--no-rag` for A/B) |
| `rag_server.py` | OpenAI-compatible **proxy** on `:8090` — what PhpStorm / clients point at |

## Setup
```bash
source ../mlx-env/bin/activate
pip install -r requirements.txt          # chromadb
python ingest.py                         # build the vector store (downloads the MiniLM embedder once)
```
The model server must be running: `../serving/serve.sh` (mlx_lm.server on :8080).

## Use it
```bash
# A/B: see the model get a fact wrong alone, then right with RAG
python ask.py --no-rag "When should I use a plugin versus a preference?"
python ask.py         "When should I use a plugin versus a preference?"

# Run the OpenAI-compatible RAG proxy (for IDEs / web UIs / curl)
python rag_server.py                     # http://127.0.0.1:8090/v1
curl -s http://127.0.0.1:8090/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"plugin vs preference?"}]}'
```

## PhpStorm (JetBrains) integration
`mlx_lm.server` and this proxy are OpenAI-API-compatible, so any plugin that allows a custom base URL works.

**Recommended: the Continue plugin** (free, JetBrains Marketplace). `~/.continue/config.yaml`:
```yaml
models:
  - name: magento-rag-chat       # chat -> RAG proxy (grounded facts + house style)
    provider: openai
    model: magento-rag
    apiBase: http://localhost:8090/v1
    apiKey: "skip"
  - name: magento-autocomplete   # FIM -> raw model (fast, no retrieval overhead)
    provider: openai
    model: qwen2.5-coder-7b-magento-v2
    apiBase: http://localhost:8080/v1
    apiKey: "skip"
    roles: [autocomplete]
```
Design: **chat → :8090 (RAG)**, **autocomplete → :8080 (raw, low latency)**. The proxy auto-detects and
overrides the model id, so the `model` field you send to `:8090` can be anything. Alternatives: the
built-in **JetBrains AI Assistant** (Settings → Tools → AI Assistant → OpenAI-compatible provider) or the
**ProxyAI** plugin. Note: `mlx_lm.server` is dev-grade — local use only.

## Growing the knowledge base
Drop more `##`-sectioned markdown into `knowledge/` and re-run `python ingest.py`. **Privacy:** keep the
committed corpus generic. Any docs built from your own/client modules should stay **local and
uncommitted** (and never uploaded to HF) — the `.chroma/` store is gitignored.
