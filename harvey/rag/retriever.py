"""RAG Retriever module for grounding Harvey's cold email drafting."""

from __future__ import annotations

import logging
from typing import Any, Optional

from harvey.rag.store import VectorStore

logger = logging.getLogger("harvey.rag.retriever")


class RAGRetriever:
    """Retrieves relevant verified product knowledge chunks to ground Writer prompts."""

    def __init__(self, store: Optional[VectorStore] = None):
        self.store = store or VectorStore()

    async def retrieve_for_prospect(
        self,
        prospect: Any,
        company: Any = None,
        top_k: int = 3,
        min_score: float = 0.2,
    ) -> list[dict[str, Any]]:
        """Formulate a contextual query based on prospect facts and retrieve top chunks."""
        if self.store.count() == 0:
            return []

        # Build query focused on prospect's domain and business context
        query_parts = []
        if getattr(prospect, "industry", None):
            query_parts.append(f"{prospect.industry} practice solutions")
        if getattr(prospect, "title", None):
            query_parts.append(f"workflow for {prospect.title}")
        if company and getattr(company, "description", None):
            query_parts.append(company.description[:120])
        if getattr(prospect, "personalization_notes", None):
            query_parts.append(prospect.personalization_notes[:100])

        query = " ".join(query_parts).strip()
        if not query:
            query = "product features capabilities overview"

        try:
            chunks = await self.store.search(query=query, top_k=top_k)
            # Filter by minimum relevance threshold
            relevant = [c for c in chunks if c.get("score", 0.0) >= min_score]
            return relevant or chunks[:1] if chunks else []
        except Exception as e:
            logger.warning(f"RAG retrieval error: {e}")
            return []

    @staticmethod
    def format_prompt_context(chunks: list[dict[str, Any]]) -> str:
        """Format retrieved knowledge chunks into strict prompt guidance with source citations."""
        if not chunks:
            return (
                "\n\nPRODUCT KNOWLEDGE (NO LOCAL RAG CHUNKS MATCHED)\n"
                "No specific verified product documents were retrieved for this prospect.\n"
                "- Use ONLY the basic product description and benefits defined in your config.\n"
                "- Do NOT invent specific product features, integrations, pricing, or timelines.\n"
                "- If a capability is not explicitly documented, do NOT claim it exists.\n"
            )

        context_lines = []
        for i, chunk in enumerate(chunks, 1):
            src = chunk.get("source", "unknown")
            pg = chunk.get("page", 1)
            cid = chunk.get("chunk_id", f"c{i}")
            txt = chunk.get("text", "").strip()
            context_lines.append(
                f"--- [Fact Source: {src} | Page: {pg} | Chunk: {cid}] ---\n{txt}"
            )

        joined_facts = "\n\n".join(context_lines)
        return (
            f"\n\nVERIFIED PRODUCT KNOWLEDGE (GROUNDED RAG CONTEXT)\n"
            f"The following excerpts are verified from internal product documentation.\n"
            f"Use ONLY these documented facts for product, feature, and capability claims:\n\n"
            f"{joined_facts}\n\n"
            f"STRICT RAG GROUNDING RULES:\n"
            f"1. Every capability claim in your email must be directly supported by the excerpts above.\n"
            f"2. Do NOT invent pricing tiers, feature promises, calendar integrations, or meeting dates.\n"
            f"3. If a prospect-specific feature is not mentioned above, omit the claim or state that it requires verification.\n"
        )

