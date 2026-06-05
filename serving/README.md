# Serving

## Run the model server
```bash
bash serving/serve.sh    # mlx_lm.server on http://127.0.0.1:8080  (--temp 0.3 --top-p 0.95)
```
OpenAI-compatible: `POST /v1/chat/completions`, `GET /v1/models`. **Sampling defaults are baked in** —
greedy decoding (`temp 0`) makes the model loop, so always sample (`temperature ~0.3`,
`repetition_penalty ~1.1`).

**Model-id gotcha:** the request `model` field must equal the id from `GET /v1/models` (the full model
path) or `mlx_lm.server` tries to download it from Hugging Face. The RAG proxy (`rag/rag_server.py`)
auto-detects and injects it, so clients/PhpStorm never need the path.

## PhpStorm / JetBrains (Continue plugin)
`phpstorm-continue.yaml` is the **template**. Install:
1. PhpStorm → Settings → Plugins → install **Continue**, restart.
2. Copy it to `~/.continue/config.yaml`; set the autocomplete `model:` to your absolute model path
   (`curl -s localhost:8080/v1/models`).
3. Start both servers (`serving/serve.sh` and `python rag/rag_server.py`), then in Continue pick
   **`Qwen2.5-Coder-Magento2 · Chat (RAG)`**.

- **Chat** → RAG proxy `:8090` (grounded with Magento facts + house style).
- **Autocomplete** → raw model `:8080` (fast inline FIM, no retrieval overhead).

See the repo-root `README.md` for the full pipeline and `rag/README.md` for the RAG layer.
