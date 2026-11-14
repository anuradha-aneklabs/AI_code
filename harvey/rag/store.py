"""ChromaDB local vector store wrapper for Harvey RAG."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings

from harvey.rag.ingest import DocumentChunk
from harvey.rag.embeddings import OllamaEmbeddings

logger = logging.getLogger("harvey.rag.store")

DEFAULT_RAG_DIR = Path("data/rag")
DEFAULT_CHROMA_DIR = DEFAULT_RAG_DIR / "chroma"
COLLECTION_NAME = "harvey_knowledge"


class VectorStore:
    """Manages persistent local vector storage via ChromaDB."""

    def __init__(
        self,
        persist_dir: Path | str = DEFAULT_CHROMA_DIR,
        embeddings: Optional[OllamaEmbeddings] = None,
    ):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embeddings = embeddings or OllamaEmbeddings()

        # Initialize ChromaDB persistent client
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False, is_persistent=True),
        )
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    async def add_chunks(self, chunks: list[DocumentChunk]) -> int:
        """Embed and upsert document chunks into the Chroma collection."""
        if not chunks:
            return 0

        texts = [c.text for c in chunks]
        ids = [c.chunk_id for c in chunks]
        metadatas = [c.to_metadata() for c in chunks]

        # Generate embeddings via Ollama
        vectors = await self.embeddings.embed_documents(texts)
        has_vectors = bool(vectors and any(v for v in vectors))

        # Check expected collection dimension if collection already has embeddings
        expected_dim = None
        if self.collection.count() > 0:
            try:
                sample = self.collection.get(include=["embeddings"], limit=1)
                if sample.get("embeddings") is not None and len(sample["embeddings"]) > 0:
                    expected_dim = len(sample["embeddings"][0])
            except Exception:
                pass

        if has_vectors:
            self.collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
                embeddings=vectors,
            )
        elif expected_dim:
            # Deterministic hash embeddings matching collection dimension when AI embedder is offline
            import hashlib, math
            def _text_to_vec(t: str, dim: int) -> list[float]:
                v = [0.0] * dim
                for w in t.lower().split():
                    h = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
                    v[h % dim] += 1.0
                norm = math.sqrt(sum(x * x for x in v)) or 1.0
                return [x / norm for x in v]
            fallback_vectors = [_text_to_vec(t, expected_dim) for t in texts]
            self.collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
                embeddings=fallback_vectors,
            )
        else:
            # Upsert without embeddings (Chroma uses default or text-only)
            self.collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
            )

        logger.info(f"RAG: Indexed {len(chunks)} chunk(s) from {[c.source for c in chunks[:1]]}")
        return len(chunks)

    async def search(
        self,
        query: str,
        top_k: int = 3,
        source_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Perform semantic search against indexed knowledge chunks."""
        query = query.strip()
        if not query or self.collection.count() == 0:
            return []

        where_filter = {"source": source_filter} if source_filter else None
        query_vector = await self.embeddings.embed_query(query)

        results = None
        try:
            if query_vector:
                results = self.collection.query(
                    query_embeddings=[query_vector],
                    n_results=min(top_k, self.collection.count()),
                    where=where_filter,
                )
            else:
                results = self.collection.query(
                    query_texts=[query],
                    n_results=min(top_k, self.collection.count()),
                    where=where_filter,
                )
        except Exception as e:
            logger.warning(f"ChromaDB search query failed: {e}; attempting lexical fallback search...")

        # If ChromaDB query failed (e.g. dimension mismatch when embedder is offline), perform lexical ranking
        if not results or not results.get("ids") or not results["ids"][0]:
            try:
                import re
                all_data = self.collection.get(include=["documents", "metadatas"], where=where_filter)
                if all_data and all_data.get("ids"):
                    q_lower = query.lower()
                    q_words = set(re.findall(r"\w+", q_lower))
                    scored_docs = []
                    for doc_id, meta, doc in zip(all_data["ids"], all_data["metadatas"], all_data["documents"]):
                        doc_lower = doc.lower()
                        doc_words = set(re.findall(r"\w+", doc_lower))
                        src_words = set(re.findall(r"\w+", meta.get("source", "").lower()))
                        overlap = len(q_words & doc_words) + 2 * len(q_words & src_words)
                        if q_lower in doc_lower:
                            overlap += 5  # Exact phrase bonus
                        if overlap > 0:
                            scored_docs.append((overlap, doc_id, doc, meta))
                    scored_docs.sort(key=lambda x: x[0], reverse=True)
                    if scored_docs:
                        return [
                            {
                                "chunk_id": d[1],
                                "text": d[2],
                                "source": d[3].get("source", "unknown"),
                                "page": d[3].get("page", 1),
                                "chunk_index": d[3].get("chunk_index", 1),
                                "file_type": d[3].get("file_type", "txt"),
                                "score": round(min(1.0, 0.5 + 0.1 * d[0]), 4),
                            }
                            for d in scored_docs[:top_k]
                        ]
            except Exception as lex_err:
                logger.warning(f"Lexical fallback search error: {lex_err}")
            return []

        chunks_out: list[dict[str, Any]] = []
        ids = results["ids"][0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if results.get("distances") else []

        for i in range(len(ids)):
            meta = metas[i] if i < len(metas) else {}
            dist = distances[i] if i < len(distances) else 0.0
            score = round(1.0 - dist, 4) if dist is not None else 1.0
            chunks_out.append(
                {
                    "chunk_id": ids[i],
                    "text": docs[i] if i < len(docs) else "",
                    "source": meta.get("source", "unknown"),
                    "page": meta.get("page", 1),
                    "chunk_index": meta.get("chunk_index", 1),
                    "file_type": meta.get("file_type", "txt"),
                    "score": score,
                }
            )

        return chunks_out

    def count(self) -> int:
        """Total number of indexed chunks in vector store."""
        return self.collection.count()

    def list_documents(self) -> list[dict[str, Any]]:
        """Return distinct indexed document files with chunk counts."""
        if self.collection.count() == 0:
            return []

        all_meta = self.collection.get(include=["metadatas"])
        metas = all_meta.get("metadatas") or []

        docs_map: dict[str, dict[str, Any]] = {}
        for m in metas:
            if not isinstance(m, dict):
                continue
            src = m.get("source", "unknown")
            if src not in docs_map:
                docs_map[src] = {
                    "source": src,
                    "file_type": m.get("file_type", "txt"),
                    "chunks": 0,
                    "pages": set(),
                    "ingested_at": m.get("ingested_at", ""),
                }
            docs_map[src]["chunks"] += 1
            if m.get("page"):
                docs_map[src]["pages"].add(m["page"])

        out = []
        for k, v in docs_map.items():
            out.append(
                {
                    "source": v["source"],
                    "file_type": v["file_type"],
                    "chunk_count": v["chunks"],
                    "page_count": len(v["pages"]) or 1,
                    "ingested_at": v["ingested_at"],
                }
            )
        return out

    def delete_document(self, source: str) -> int:
        """Delete all chunks belonging to a specific source document."""
        if not source:
            return 0
        current_count = self.collection.count()
        self.collection.delete(where={"source": source})
        deleted = current_count - self.collection.count()
        logger.info(f"RAG: Deleted {deleted} chunk(s) for source '{source}'.")
        return deleted

    def clear(self):
        """Reset the vector collection completely."""
        self.client.delete_collection(COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )

