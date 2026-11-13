"""Harvey Local RAG Package."""

from harvey.rag.embeddings import OllamaEmbeddings
from harvey.rag.ingest import DocumentChunk, ingest_document, chunk_text
from harvey.rag.store import VectorStore
from harvey.rag.retriever import RAGRetriever
from harvey.rag.trace import record_rag_trace, get_rag_trace, list_rag_traces

__all__ = [
    "OllamaEmbeddings",
    "DocumentChunk",
    "ingest_document",
    "chunk_text",
    "VectorStore",
    "RAGRetriever",
    "record_rag_trace",
    "get_rag_trace",
    "list_rag_traces",
]

