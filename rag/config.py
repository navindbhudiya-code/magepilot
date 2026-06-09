"""Central configuration for the Magento RAG layer."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# --- Local fine-tuned model server (mlx_lm.server, OpenAI-compatible) ---
# The server identifies the model by its full path; ask.py / rag_server.py auto-detect
# the real id from /v1/models (using MODEL_MATCH), so nothing hardcodes the path.
# MODEL_SERVER env points the stack at any OpenAI-compatible server (Ollama/vLLM/llama.cpp).
MODEL_SERVER = os.environ.get("MODEL_SERVER", "http://127.0.0.1:8080/v1")
MODEL_MATCH = "magento"          # substring used to pick the fine-tune from /v1/models

# --- Vector store (ChromaDB) ---
CHROMA_PATH = os.path.join(HERE, ".chroma")
COLLECTION = "magento_kb"
KNOWLEDGE_DIR = os.path.join(HERE, "knowledge")

# --- Retrieval ---
TOP_K = 4

# --- Generation (MUST be sampling, not greedy — greedy decoding loops) ---
SAMPLING = {
    "temperature": 0.3,
    "top_p": 0.95,
    "repetition_penalty": 1.1,
    "max_tokens": 700,
}

# --- RAG proxy (OpenAI-compatible; point PhpStorm / clients here) ---
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8090
