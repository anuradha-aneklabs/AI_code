"""Local Ollama embeddings client for Harvey RAG."""

from __future__ import annotations

import logging
from typing import Any
import httpx

logger = logging.getLogger("harvey.rag.embeddings")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


class OllamaEmbeddings:
    """Generates vector embeddings using local Ollama instance."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_EMBED_MODEL):
        self.base_url = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.model = model

    async def is_available(self) -> bool:
        """Check if embedding model is pulled and ready in Ollama."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                if res.status_code == 200:
                    models = res.json().get("models") or []
                    names = [m.get("name", "") for m in models]
                    return any(self.model in name for name in names)
        except Exception:
            pass
        return False

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query text."""
        return await self._get_embedding(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunk texts."""
        embeddings = []
        for t in texts:
            emb = await self._get_embedding(t)
            embeddings.append(emb)
        return embeddings

    async def _get_embedding(self, text: str) -> list[float]:
        """Fetch embedding vector from Ollama API."""
        if not text.strip():
            return []
        
        # Try /api/embeddings (standard endpoint in Ollama)
        url = f"{self.base_url}/api/embeddings"
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": text,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    emb = data.get("embedding")
                    if isinstance(emb, list) and emb:
                        return [float(x) for x in emb]
        except Exception as e:
            logger.warning(f"Ollama embeddings error for '{self.model}': {e}")

        # Fallback to /api/embed if newer Ollama version
        try:
            url_embed = f"{self.base_url}/api/embed"
            payload_embed: dict[str, Any] = {
                "model": self.model,
                "input": text,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(url_embed, json=payload_embed)
                if res.status_code == 200:
                    data = res.json()
                    embs = data.get("embeddings")
                    if isinstance(embs, list) and embs and isinstance(embs[0], list):
                        return [float(x) for x in embs[0]]
        except Exception:
            pass

        return []

